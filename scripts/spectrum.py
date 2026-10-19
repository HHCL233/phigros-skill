#!/usr/bin/env python3
"""频谱分析：把整首音乐转成频谱图，供写谱采音/看旋律与鼓点。

输出（默认写在音频同目录）：
  <名>_spectrum.png   频谱图（STFT 谱图，纵轴频率、横轴时间，带八度/时间网格线）
  <名>_peaks.csv      逐时段主频（含音名），用于定位旋律与打点
  stdout              摘要 JSON：频段能量占比、谱质心、全曲主频、图像坐标映射

用法：
  python3 spectrum.py <音频>                          # 默认 log 频率轴，20Hz~奈奎斯特
  python3 spectrum.py <音频> --start 64 --end 96     # 只看 64~96s 段落
  python3 spectrum.py <音频> --scale linear --height 800
  python3 spectrum.py <音频> --no-png --no-csv       # 只要摘要 JSON

依赖：numpy（必需）、ffmpeg（非 WAV 音频必需）。PNG 由脚本自带编码器生成，不依赖 matplotlib/PIL。
"""

import argparse
import json
import math
import os
import struct
import subprocess
import sys
import wave
import zlib

import numpy as np

DEFAULT_SR = 22050
BANDS = (("sub", 20, 60), ("low", 60, 250), ("mid", 250, 2000), ("high", 2000, 8000), ("air", 8000, 16000))
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# viridis 近似锚点（RGB 0-255）
VIRIDIS = [(68, 1, 84), (72, 35, 116), (64, 67, 135), (52, 94, 141), (41, 120, 142),
           (32, 144, 140), (34, 167, 132), (68, 190, 109), (121, 209, 81), (189, 222, 38), (253, 231, 37)]
GRID_RGB = (150, 150, 150)
BORDER_RGB = (220, 220, 220)


def decode(path, sr, start=None, end=None):
    """解码为单声道 float32；返回 (波形, sr)。"""
    if path.lower().endswith(".wav") and start is None and end is None:
        with wave.open(path, "rb") as w:
            src_sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
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
        if src_sr != sr:  # 线性重采样到目标采样率
            idx = np.arange(0, len(data), src_sr / sr)
            data = np.interp(idx, np.arange(len(data)), data).astype(np.float32)
        return data, sr

    cmd = ["ffmpeg", "-v", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", path]
    if end is not None:
        cmd += ["-t", str(max(end - (start or 0), 0.01))]
    cmd += ["-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError:
        print("ERROR: 未找到 ffmpeg，无法解码非 WAV 音频（apt/pacman 安装 ffmpeg）", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print("ERROR: ffmpeg 解码失败: %s" % e.stderr.decode(errors="ignore"), file=sys.stderr)
        sys.exit(1)
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0, sr


def spectrogram_png(path, db, freqs, times, args):
    """把 dB 谱图渲染成 PNG，返回实际使用的频率范围。"""
    h, w = args.height, args.width
    nyq = freqs[-1]
    f_hi = min(args.fmax, nyq)
    f_lo = min(args.fmin, f_hi * 0.99)

    # ---- 频率轴（行）映射：log 用等比边界，linear 用等差边界 ----
    if args.scale == "log":
        ratio = (f_hi / f_lo) ** (1.0 / h)
        edges = f_lo * ratio ** np.arange(h + 1)          # 行 0 为最高频
    else:
        edges = np.linspace(f_lo, f_hi, h + 1)
    lower = edges[h - 1 - np.arange(h)]                   # 行 0 在最上：下界自高到低
    upper = edges[h - np.arange(h)]                       # 行 0 在最上：上界自高到低
    lo_b = np.searchsorted(freqs, lower)                  # 每行的频率下界（bin 下标）
    hi_b = np.maximum(np.clip(np.searchsorted(freqs, upper, side="right") - 1,
                              0, len(freqs) - 1), lo_b)   # 每行的频率上界（至少含 1 个 bin）
    row_db = np.zeros((h, db.shape[0]), dtype=np.float32)
    for i in range(h):                                     # 行数 ~600，逐行 max 池化开销可接受
        row_db[i] = db[:, lo_b[i]:hi_b[i] + 1].max(axis=1)

    # ---- 时间轴（列）映射：按列 max 池化 ----
    n = db.shape[0]
    col_edges = np.linspace(0, n, w + 1).astype(int)
    img_db = np.zeros((h, w), dtype=np.float32)
    for j in range(w):
        a, b = col_edges[j], max(col_edges[j + 1], col_edges[j] + 1)
        img_db[:, j] = row_db[:, a:min(b, n)].max(axis=1)

    # ---- dB -> 颜色 ----
    norm = np.clip((img_db - args.db_floor) / -args.db_floor, 0.0, 1.0)
    idx = np.clip((norm * 255).astype(np.int32), 0, 255)
    lut = colormap_lut()
    rgb = lut[idx]                                         # (h, w, 3)

    # ---- 网格：八度线 + 时间线 + 边框 ----
    if args.scale == "log":
        for f in (32, 55, 110, 220, 440, 880, 1760, 3520, 7040, 14080):
            if f_lo < f < f_hi:
                r = h - 1 - int(round(math.log(f / f_lo) / math.log(f_hi / f_lo) * (h - 1)))
                if 0 <= r < h:
                    rgb[r] = GRID_RGB
    else:
        span = f_hi - f_lo
        for f in range(int(f_lo // 1000 * 1000) + 1000, int(f_hi), 1000):
            r = h - 1 - int(round((f - f_lo) / span * (h - 1)))
            if 0 <= r < h:
                rgb[r] = GRID_RGB
    dur = times[-1] - times[0] if len(times) > 1 else 0.0
    step = 10.0 if dur >= 60 else max(1.0, round(dur / 8))
    for t in np.arange(0.0, dur + 1e-6, step):
        c = int(round(t / max(dur, 1e-6) * (w - 1)))
        if 0 <= c < w:
            rgb[:, c] = GRID_RGB
    rgb[0] = rgb[-1] = BORDER_RGB
    rgb[:, 0] = rgb[:, -1] = BORDER_RGB

    write_png(path, rgb)
    return f_lo, f_hi


def colormap_lut():
    """256 级 viridis 查表。"""
    anchors = np.array(VIRIDIS, dtype=np.float32)
    xs = np.linspace(0, 1, len(anchors))
    t = np.linspace(0, 1, 256)
    lut = np.stack([np.interp(t, xs, anchors[:, c]) for c in range(3)], axis=1)
    return lut.astype(np.uint8)


def write_png(path, rgb):
    """numpy uint8 (h,w,3) -> PNG（自带编码器，无需 PIL/matplotlib）。"""
    h, w = rgb.shape[:2]
    raw = np.concatenate([np.zeros((h, 1), dtype=np.uint8), rgb.reshape(h, w * 3)], axis=1).tobytes()

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def freq_to_note(f):
    """频率 -> (音名, 音分偏差)。"""
    midi = 69.0 + 12.0 * math.log2(f / 440.0)
    m = int(round(midi))
    return NOTE_NAMES[m % 12] + str(m // 12 - 1), int(round((midi - m) * 100))


def slice_peaks(db, freqs, times, slice_sec, top):
    """逐时段主频（谱峰），返回按时间排序的记录列表。"""
    n = db.shape[0]
    if n == 0:
        return []
    hop_sec = (times[1] - times[0]) if n > 1 else 1.0
    win = max(int(round(slice_sec / max(hop_sec, 1e-9))), 1)
    rows = []
    for start in range(0, n, win):
        avg_db = db[start:start + win].mean(axis=0)
        order = np.argsort(avg_db)[::-1]
        picked = []
        for b in order:
            f = float(freqs[b])
            if f < 20 or avg_db[b] - avg_db.max() < -70:
                continue
            if b > 0 and b + 1 < len(avg_db) and (avg_db[b] < avg_db[b - 1] or avg_db[b] < avg_db[b + 1]):
                continue  # 只取局部极大，滤掉 Hann 窗旁瓣
            if any(abs(math.log2(f / pf)) < 0.05 for pf, _, _, _ in picked):  # 与已选峰相距 < 约半个半音
                continue
            note, cents = freq_to_note(f)
            picked.append((f, note, cents, float(avg_db[b])))
            if len(picked) >= top:
                break
        t0 = float(times[start])
        rows.append((round(t0, 3), picked))
    return rows


def band_share(db, freqs):
    """各频段能量占比（%)。"""
    power = (10.0 ** (db / 10.0))
    total = float(power.sum())
    out = {}
    for name, lo, hi in BANDS:
        sel = (freqs >= lo) & (freqs < hi)
        out[name] = round(float(power[:, sel].sum()) / total * 100.0, 2) if total > 0 else 0.0
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="分析音乐并生成频谱图（写谱采音辅助）")
    ap.add_argument("audio")
    ap.add_argument("--sr", type=int, default=DEFAULT_SR, help="分析采样率（默认 22050）")
    ap.add_argument("--n-fft", type=int, default=2048, help="FFT 窗长（默认 2048，越大频率越细）")
    ap.add_argument("--hop", type=int, default=512, help="帧移（默认 512，越小时间越细）")
    ap.add_argument("--scale", choices=("log", "linear"), default="log", help="频率轴（默认 log）")
    ap.add_argument("--fmin", type=float, default=20.0, help="图像最低频率 Hz（默认 20）")
    ap.add_argument("--fmax", type=float, default=11025.0, help="图像最高频率 Hz（默认 11025，自动不超过奈奎斯特）")
    ap.add_argument("--db-floor", type=float, default=-80.0, help="颜色下限 dB（默认 -80，相对峰值）")
    ap.add_argument("--width", type=int, default=1400, help="图像宽（默认 1400）")
    ap.add_argument("--height", type=int, default=600, help="图像高（默认 600）")
    ap.add_argument("--start", type=float, default=None, help="起始秒（只分析该段落）")
    ap.add_argument("--end", type=float, default=None, help="结束秒")
    ap.add_argument("--slice", type=float, default=0.5, help="主频 CSV 的时间粒度秒（默认 0.5）")
    ap.add_argument("--top", type=int, default=5, help="每时段列出的主频个数（默认 5）")
    ap.add_argument("--out-dir", default=None, help="输出目录（默认音频所在目录）")
    ap.add_argument("--no-png", action="store_true", help="不输出频谱图")
    ap.add_argument("--no-csv", action="store_true", help="不输出主频 CSV")
    args = ap.parse_args(argv)

    if not os.path.exists(args.audio):
        print("ERROR: 找不到音频文件 %s" % args.audio, file=sys.stderr)
        return 1
    stem = os.path.splitext(os.path.basename(args.audio))[0]
    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.audio))
    os.makedirs(out_dir, exist_ok=True)

    y, sr = decode(args.audio, args.sr, args.start, args.end)
    if len(y) < 16:
        print("ERROR: 音频过短或无有效数据（%d 采样）" % len(y), file=sys.stderr)
        return 1

    n_fft = max(64, min(args.n_fft, len(y)))
    hop = max(1, min(args.hop, n_fft))
    win = np.hanning(n_fft).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(y, n_fft)[::hop]
    spec = np.abs(np.fft.rfft(frames * win, axis=1)) * (2.0 / win.sum())
    db = 20.0 * np.log10(np.maximum(spec, 1e-10))
    db -= db.max()
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    times = np.arange(frames.shape[0]) * (hop / float(sr)) + (args.start or 0.0)

    png_name = csv_name = None
    f_lo, f_hi = None, None
    if not args.no_png:
        png_name = os.path.join(out_dir, "%s_spectrum.png" % stem)
        f_lo, f_hi = spectrogram_png(png_name, db, freqs, times, args)

    peaks = slice_peaks(db, freqs, times, args.slice, max(1, args.top))
    if not args.no_csv:
        csv_name = os.path.join(out_dir, "%s_peaks.csv" % stem)
        with open(csv_name, "w", encoding="utf-8") as f:
            head = ["time_sec"]
            for i in range(1, max(1, args.top) + 1):
                head += ["freq%d_hz" % i, "note%d" % i, "cents%d" % i, "db%d" % i]
            f.write(",".join(head) + "\n")
            for t, pk in peaks:
                row = ["%.3f" % t]
                for f_, note, cents, mag in pk:
                    row += ["%.1f" % f_, note, str(cents), "%.1f" % mag]
                row += [""] * (len(head) - len(row))
                f.write(",".join(row) + "\n")

    # 全曲主频（对平均谱取峰）
    avg_db = db.mean(axis=0)
    order = np.argsort(avg_db)[::-1]
    global_peaks = []
    for b in order:
        f_ = float(freqs[b])
        if f_ < 20:
            continue
        if 0 < b < len(avg_db) - 1 and (avg_db[b] < avg_db[b - 1] or avg_db[b] < avg_db[b + 1]):
            continue
        if any(abs(math.log2(f_ / g["freq_hz"])) < 0.05 for g in global_peaks):
            continue
        note, cents = freq_to_note(f_)
        global_peaks.append({"freq_hz": round(f_, 1), "note": note, "cents": cents, "db": round(float(avg_db[b]), 1)})
        if len(global_peaks) >= 10:
            break

    centroid = np.sum(freqs[None, :] * (10.0 ** (db / 10.0)), axis=1) / np.maximum(
        np.sum(10.0 ** (db / 10.0), axis=1), 1e-20)
    summary = {
        "audio": os.path.abspath(args.audio),
        "duration_sec": round(len(y) / sr, 3),
        "start_sec": args.start,
        "sample_rate": sr,
        "n_fft": n_fft,
        "hop": hop,
        "frames": int(frames.shape[0]),
        "png": png_name,
        "csv": csv_name,
        "png_axis": None if f_lo is None else {
            "x": "%.3f~%.3f s" % (float(times[0]), float(times[-1])),
            "y": "%.0f~%.0f Hz (%s)" % (f_lo, f_hi, args.scale),
            "size": "%dx%d" % (args.width, args.height),
            "grid_x": "每 %gs 一条竖线" % (10.0 if len(y) / sr >= 60 else max(1.0, round((len(y) / sr) / 8))),
            "grid_y": "log 轴：32/55/110/220/440/880/1760/3520/7040/14080Hz 横线（每八度一条）"
                      if args.scale == "log" else "linear 轴：每 1000Hz 一条横线",
        },
        "band_share_percent": band_share(db, freqs),
        "spectral_centroid_hz": {"median": round(float(np.median(centroid)), 1),
                                 "p10": round(float(np.percentile(centroid, 10)), 1),
                                 "p90": round(float(np.percentile(centroid, 90)), 1)},
        "global_peaks": global_peaks,
        "slice_peaks_file": csv_name,
        "hint": "逐时段能量/起音/音高请配合 segment_analyze.py（0.2s 粒度）使用",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
