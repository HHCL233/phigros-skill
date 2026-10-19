#!/usr/bin/env python3
"""把音频按固定时长（默认 0.2 秒）切片并逐片分析，输出 AI 写谱用的采音表。

每片给出：
  响度    RMS / 峰值 dBFS（段落能量、留白与高潮识别）
  频段    低频(30~200Hz，鼓点) / 中频(200~2000Hz，旋律人声) / 高频(2000~8000Hz，镲片齿音)
  质心    谱质心 Hz（音色明暗）
  起音    onset 相对强度 z 分数（找打点位置）
  基频    自相关估计的频率与音名（旋律走向参考，无声/噪声片为 null）
  归一    各指标的 0~1 归一值（便于按阈值筛片）

用法：
  python3 segment_analyze.py <音频> [--interval 0.2] [--out-dir <目录>]
        [--export-wavs <目录>] [--onset-threshold 1.5] [--top 40]

输出：
  <out-dir>/<音频名>_segments.json   完整逐片数据
  <out-dir>/<音频名>_segments.csv    同上的表格形式（便于 AI 检索）
  stdout                              摘要 JSON（响度 sparkline / onset 候选打点时刻 / 音域）
  --export-wavs <目录>                额外导出每片 0.2s 的 wav 切片（文件多，按需开启）

WAV 直接用标准库解码；MP3/OGG/FLAC 等需系统安装 ffmpeg。
起音/基频为辅助估计，最终打点仍须用 time_conv 吸附到拍并同用户确认 BPM/offset。
"""
import argparse
import csv
import json
import math
import os
import subprocess
import sys
import wave

try:
    import numpy as np
except ImportError:
    print("ERROR: 需要 numpy（pip install numpy）", file=sys.stderr)
    sys.exit(1)

DEFAULT_SR = 22050
BANDS = (("low", 30, 200), ("mid", 200, 2000), ("high", 2000, 8000))
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
SPARK = " ▁▂▃▄▅▆▇█"


def decode(path):
    """返回 (mono float32 [-1,1], sr)。"""
    if path.lower().endswith(".wav"):
        with wave.open(path, "rb") as w:
            sr = w.getframerate()
            ch = w.getnchannels()
            sw = w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if sw == 2:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif sw == 4:
            data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        elif sw == 1:
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            raise ValueError("不支持的采样位宽 %d" % sw)
        if ch > 1:
            data = data.reshape(-1, ch).mean(axis=1)
        return data, sr
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(DEFAULT_SR),
             "-f", "s16le", "-"],
            capture_output=True, check=True)
    except FileNotFoundError:
        print("ERROR: 未找到 ffmpeg，无法解码非 WAV 音频（apt/pacman 安装 ffmpeg）", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print("ERROR: ffmpeg 解码失败: %s" % e.stderr.decode(errors="ignore"), file=sys.stderr)
        sys.exit(1)
    data = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    return data, DEFAULT_SR


def to_db(x):
    return 10.0 * math.log10(max(x, 1e-20))


def freq_to_note(f):
    """频率 -> (音名, 音分偏差)。"""
    midi = 69.0 + 12.0 * math.log2(f / 440.0)
    m = int(round(midi))
    return NOTE_NAMES[m % 12] + str(m // 12 - 1), int(round((midi - m) * 100))


def estimate_pitch(x, sr):
    """自相关基频估计，返回 (频率, 强度) 或 (None, 0)。"""
    if len(x) < 64:
        return None, 0.0
    x = x - x.mean()
    if np.sqrt(np.mean(x * x)) < 1e-4:
        return None, 0.0
    # 单次 FFT：零填充到 2N 后高通 60Hz（压低底鼓），再做 FFT 自相关（避免 O(N^2)）
    n = len(x)
    spec = np.fft.rfft(x, 2 * n)
    spec = spec * (np.fft.rfftfreq(2 * n, 1.0 / sr) >= 60.0)
    ac = np.fft.irfft(np.abs(spec) ** 2)[:n]
    if ac[0] <= 0:
        return None, 0.0
    ac = ac / ac[0]
    lag_min = max(1, int(sr / 1000.0))
    lag_max = min(len(ac) - 2, int(sr / 55.0))
    if lag_max <= lag_min:
        return None, 0.0
    seg = ac[lag_min:lag_max]
    i = int(seg.argmax())
    val = float(seg[i])
    if val < 0.35:
        return None, val
    # 抛物线插值细化峰值位置
    lag = lag_min + i
    a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
    denom = a - 2 * b + c
    shift = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
    shift = float(max(-1.0, min(1.0, shift)))
    f = float(sr / (lag + shift))
    return (f if 55.0 <= f <= 1000.0 else None), val


def plain(obj):
    """把 numpy 标量转成 Python 原生类型，便于 JSON 序列化。"""
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    if hasattr(obj, "item"):
        return obj.item()
    return obj


def normalize(vals):
    """映射到 0~1 并截断极值：以 5%/95% 分位为界，避免静音段拉爆量程；全等时给 0.5。"""
    a = np.asarray(vals, dtype=np.float64)
    lo, hi = (float(v) for v in np.percentile(a, [5, 95]))
    if hi - lo < 1e-9:
        return [0.5] * len(vals)
    return [min(1.0, max(0.0, (float(v) - lo) / (hi - lo))) for v in a]


def analyze(y, sr, interval):
    seg_len = max(1, int(round(interval * sr)))
    n = max(1, (len(y) + seg_len - 1) // seg_len)
    win = np.hanning(seg_len)
    rows = []
    prev_logspec = None
    for i in range(n):
        s = i * seg_len
        raw = y[s:s + seg_len]
        raw = np.pad(raw, (0, seg_len - len(raw))) if len(raw) < seg_len else raw
        rms = float(np.sqrt(np.mean(raw * raw)))
        peak = float(np.max(np.abs(raw))) if len(raw) else 0.0
        spec = np.abs(np.fft.rfft(raw * win))
        power = spec * spec
        freqs = np.fft.rfftfreq(seg_len, 1.0 / sr)
        total = float(power.sum())
        bands = {}
        for name, lo, hi in BANDS:
            m = (freqs >= lo) & (freqs < hi)
            bands[name] = to_db(float(power[m].sum()))
        centroid = float((freqs * power).sum() / total) if total > 0 else 0.0
        logspec = np.log1p(20.0 * spec)
        flux = float(np.maximum(logspec - prev_logspec, 0).sum()) if prev_logspec is not None else 0.0
        prev_logspec = logspec
        pitch, strength = estimate_pitch(raw, sr)
        note = freq_to_note(pitch)[0] if pitch else None
        rows.append({
            "index": i,
            "t_start": round(s / sr, 4),
            "t_end": round(min(len(y), s + seg_len) / sr, 4),
            "rms_dbfs": round(20.0 * math.log10(max(rms, 1e-10)), 2),
            "peak_dbfs": round(20.0 * math.log10(max(peak, 1e-10)), 2),
            "low_dbfs": round(bands["low"], 2),
            "mid_dbfs": round(bands["mid"], 2),
            "high_dbfs": round(bands["high"], 2),
            "centroid_hz": round(centroid, 1),
            "pitch_hz": round(pitch, 1) if pitch else None,
            "pitch_note": note,
            "pitch_strength": round(strength, 3),
            "flux_raw": flux,
        })
    # 归一化：响度、频段、质心、起音
    rms_n = normalize([r["rms_dbfs"] for r in rows])
    flux_n = normalize([r["flux_raw"] for r in rows])
    centroid_n = normalize([r["centroid_hz"] for r in rows])
    low_n = normalize([r["low_dbfs"] for r in rows])
    mid_n = normalize([r["mid_dbfs"] for r in rows])
    flux_mean = float(np.mean([r["flux_raw"] for r in rows]))
    flux_std = float(np.std([r["flux_raw"] for r in rows])) or 1.0
    for i, r in enumerate(rows):
        r["onset_flux"] = round((r["flux_raw"] - flux_mean) / flux_std, 3)
        r["rms_n"] = round(rms_n[i], 3)
        r["low_n"] = round(low_n[i], 3)
        r["mid_n"] = round(mid_n[i], 3)
        r["centroid_n"] = round(centroid_n[i], 3)
        r["flux_n"] = round(flux_n[i], 3)
        del r["flux_raw"]
    return rows


def sparkline(rows):
    levels = normalize([r["rms_dbfs"] for r in rows])
    return "".join(SPARK[1 + int(v * (len(SPARK) - 2))] for v in levels)


def write_wavs(y, sr, interval, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    seg_len = max(1, int(round(interval * sr)))
    n = max(1, (len(y) + seg_len - 1) // seg_len)
    for i in range(n):
        chunk = y[i * seg_len:(i + 1) * seg_len]
        pcm = np.clip(chunk * 32767.0, -32768, 32767).astype(np.int16)
        with wave.open(os.path.join(out_dir, "seg_%05d.wav" % i), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.tobytes())
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description="按固定时长切片并逐片分析音频（写谱采音辅助）")
    ap.add_argument("audio")
    ap.add_argument("--interval", type=float, default=0.2, help="切片时长（秒，默认 0.2）")
    ap.add_argument("--out-dir", default=None, help="json/csv 输出目录（默认音频所在目录）")
    ap.add_argument("--export-wavs", default=None, metavar="目录",
                    help="额外导出每片 wav 切片到该目录")
    ap.add_argument("--onset-threshold", type=float, default=1.5,
                    help="onset 候选打点阈值（z 分数，默认 1.5）")
    ap.add_argument("--top", type=int, default=40, help="摘要中 onset 候选最多列出几条（默认 40）")
    args = ap.parse_args(argv)

    if args.interval <= 0:
        print("ERROR: --interval 必须为正数", file=sys.stderr)
        return 1

    y, sr = decode(args.audio)
    if len(y) == 0:
        print("ERROR: 音频为空", file=sys.stderr)
        return 1
    rows = analyze(y, sr, args.interval)
    duration = len(y) / sr

    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.audio))
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.audio))[0]
    json_path = os.path.join(out_dir, stem + "_segments.json")
    csv_path = os.path.join(out_dir, stem + "_segments.csv")
    meta = {
        "audio": args.audio,
        "duration_sec": round(duration, 3),
        "sr": sr,
        "interval_sec": args.interval,
        "n_segments": len(rows),
        "columns": "t_start/t_end 秒；*_dbfs 各频段能量；centroid_hz 音色明暗；"
                   "pitch_hz/pitch_note 基频估计；pitch_strength 基频置信；"
                   "onset_flux 起音强度 z 分数；*_n 为 0~1 归一值",
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(plain({"meta": meta, "segments": rows}), f, ensure_ascii=False, indent=1)
    fields = ["index", "t_start", "t_end", "rms_dbfs", "peak_dbfs", "low_dbfs", "mid_dbfs",
              "high_dbfs", "centroid_hz", "pitch_hz", "pitch_note", "pitch_strength",
              "onset_flux", "rms_n", "low_n", "mid_n", "centroid_n", "flux_n"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    hits = [r for r in rows if r["onset_flux"] >= args.onset_threshold]
    hits.sort(key=lambda r: -r["onset_flux"])
    onsets = hits[:args.top]
    pitched = [r for r in rows if r["pitch_hz"]]
    summary = {
        "audio": args.audio,
        "duration_sec": round(duration, 3),
        "sr": sr,
        "interval_sec": args.interval,
        "n_segments": len(rows),
        "rms_dbfs_max": max(r["rms_dbfs"] for r in rows),
        "rms_dbfs_min": min(r["rms_dbfs"] for r in rows),
        "rms_sparkline": sparkline(rows),
        "onset_threshold": args.onset_threshold,
        "onset_count": len(hits),
        "onset_list_truncated": len(hits) > len(onsets),
        "onset_candidates": [
            {"t": r["t_start"], "flux": r["onset_flux"],
             "low_n": r["low_n"], "mid_n": r["mid_n"]} for r in onsets],
        "pitch_voiced_segments": len(pitched),
        "pitch_notes": sorted(set(r["pitch_note"] for r in pitched)),
        "outputs": {"json": json_path, "csv": csv_path},
        "note": "t 为切片起始秒（±interval/2 误差）；用 time_conv.py 吸附到拍后据 onset 打点；"
                "低频(low)强 = 鼓点，中频(mid)+基频 = 旋律；sparkline 为全曲响度走势",
    }
    if args.export_wavs:
        cnt = write_wavs(y, sr, args.interval, args.export_wavs)
        summary["exported_wavs"] = {"dir": args.export_wavs, "count": cnt}
    print(json.dumps(plain(summary), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
