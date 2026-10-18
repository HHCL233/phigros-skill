#!/usr/bin/env python3
"""估计音频的 BPM 与起始偏移，为写谱提供采音基准。

用法：python3 bpm_detect.py <音频文件> [--min 60] [--max 200]
输出 JSON：duration_sec / bpm_candidates / suggested_offset_ms。

WAV 直接用标准库解码；MP3/OGG/FLAC 等需系统安装 ffmpeg。
结果仅供确认，最终 BPM/offset 应与用户核对。
"""
import argparse
import json
import math
import subprocess
import sys
import wave

try:
    import numpy as np
except ImportError:
    print("ERROR: 需要 numpy（pip install numpy）", file=sys.stderr)
    sys.exit(1)

SR = 22050
FRAME = 1024
HOP = 512


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
            ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
             "-f", "s16le", "-"],
            capture_output=True, check=True)
    except FileNotFoundError:
        print("ERROR: 未找到 ffmpeg，无法解码非 WAV 音频（apt/pacman 安装 ffmpeg）", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print("ERROR: ffmpeg 解码失败: %s" % e.stderr.decode(errors="ignore"), file=sys.stderr)
        sys.exit(1)
    data = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    return data, SR


def onset_envelope(y):
    n = (len(y) - FRAME) // HOP
    if n < 8:
        raise ValueError("音频太短")
    frames = np.lib.stride_tricks.as_strided(
        y, shape=(n, FRAME), strides=(y.strides[0] * HOP, y.strides[0])).copy()
    spec = np.abs(np.fft.rfft(frames * np.hanning(FRAME), axis=1))
    logspec = np.log1p(10.0 * spec)
    flux = np.maximum(logspec[1:] - logspec[:-1], 0).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    flux -= flux.mean()
    std = flux.std() or 1.0
    return flux / std, n


def tempo_candidates(oe, sr, hop, lo, top):
    fps = sr / hop
    lag_min = int(fps * 60.0 / top)
    lag_max = int(fps * 60.0 / lo)
    if lag_max >= len(oe) // 2:
        lag_max = len(oe) // 2 - 1
    ac = np.correlate(oe, oe, mode="full")[len(oe) - 1:]
    ac /= ac[0] or 1.0

    def acat(l):
        """自相关在分数滞后处的线性插值，避免整数滞后量化导致的 BPM 偏差。"""
        i = int(l)
        if i + 1 >= len(ac):
            return 0.0
        frac = l - i
        return ac[i] * (1 - frac) + ac[i + 1] * frac

    def score(bpm):
        lag = 60.0 * fps / bpm
        s = 0.0
        w = 1.0
        for mult in (1.0, 2.0, 4.0, 0.5):
            l = lag * mult
            if lag_min <= l <= lag_max:
                s += w * acat(l)
                w *= 0.5
        return s

    grid = np.arange(lo, top, 0.1)
    scores = np.array([score(b) for b in grid])
    cands = []
    for i in range(2, len(grid) - 2):
        if scores[i] >= scores[i - 1] and scores[i] >= scores[i + 1] and scores[i] > 0.05:
            # 抛物线插值细化峰值
            denom = scores[i - 1] - 2 * scores[i] + scores[i + 1]
            delta = 0.5 * (scores[i - 1] - scores[i + 1]) / denom if abs(denom) > 1e-12 else 0.0
            delta = max(-1.0, min(1.0, delta))
            b = float(grid[i] + 0.1 * delta)
            s = float(scores[i])
            if all(abs(b - p) > 0.6 for _, p in cands):
                cands.append((s, b))
    cands.sort(reverse=True)
    picked = []
    for s, b in cands:
        harmonic = False
        for p, _ in picked:
            r = b / p
            if abs(r - round(r)) < 0.03 or abs(1 / r - round(1 / r)) < 0.03:
                harmonic = True
                break
        if not harmonic:
            picked.append((b, s))
        if len(picked) >= 5:
            break
    return [{"bpm": round(b, 1), "strength": round(s, 3)} for b, s in picked]


def phase_offset(oe, sr, hop, bpm):
    fps = sr / hop
    period = 60.0 / bpm * fps
    times = np.where(oe > max(0.4 * oe.std() * 3, 0.15))[0]
    if len(times) == 0:
        return 0.0
    phases = np.mod(times, period) / fps
    best_phase, best_sum = 0.0, -1.0
    bins = max(8, int(period))
    hist, edges = np.histogram(phases, bins=bins, range=(0, period / fps))
    i = int(hist.argmax())
    best_phase = (edges[i] + edges[i + 1]) / 2
    return best_phase * 1000.0


def main(argv=None):
    ap = argparse.ArgumentParser(description="估计音频 BPM 与起始偏移")
    ap.add_argument("audio")
    ap.add_argument("--min", type=float, default=60.0)
    ap.add_argument("--max", type=float, default=240.0)
    args = ap.parse_args(argv)

    y, sr = decode(args.audio)
    duration = len(y) / sr
    oe, frames = onset_envelope(y)
    cands = tempo_candidates(oe, sr, HOP, args.min, args.max)
    best_bpm = cands[0]["bpm"] if cands else None
    off_ms = phase_offset(oe, sr, HOP, best_bpm) if best_bpm else 0.0
    print(json.dumps({
        "duration_sec": round(duration, 2),
        "bpm_candidates": cands,
        "suggested_offset_ms": round(off_ms),
        "note": "offset 为第一拍相位估计（毫秒），可直接用作 META.offset（正值 = 谱面相对音乐延后）；需与用户确认",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
