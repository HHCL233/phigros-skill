#!/usr/bin/env python3
"""旋律提取：分析指定时间区间内的音频，输出对应的旋律（音名序列 + 音符事件）。

用法：
  python3 melody.py <音频> --start 12.5 --end 34.2
  python3 melody.py <音频> --start 12.5              # 不给 --end 表示分析到音频结尾
  python3 melody.py <音频> --start 0 --end 20 --out-dir out_dir

原理（逐帧基频估计，非多音高分离）：
  加窗 → FFT 频域带通（默认 200~2000Hz，对应「中频=旋律/人声」，压掉贝斯与高频泛音）
  → 自相关基频（抛物线插值细化）→ 带通能量门限 + 自相关峰强判有无音 → 中值平滑与八度纠正
  → 合并成音符事件（丢弃过短音、合并同音相邻段）→ 输出音名序列与音符表。
低音重的段落（贝斯泛音被当成旋律音）可抬高带通下限聚焦旋律区，例如：
  --band-lo 250 --fmin 180        更靠中音区
  --band-lo 400 --fmin 300        只取高音区/主奏
输出：stdout 打印旋律时间轴与音名序列；默认另写 <音频名>_melody.json / _melody.csv（--no-files 关闭）。
时间戳为音频绝对秒，可直接与谱面 offset 换算。
"""
import argparse
import csv
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from segment_analyze import decode, freq_to_note  # noqa: E402  （同目录脚本，复用解码与音名换算）

DEFAULTS = dict(win=2048, hop=256, band_lo=200.0, band_hi=2000.0,
                fmin=100.0, fmax=1100.0, min_conf=0.40, min_dur=0.08,
                merge_gap=0.06, db_rel=30.0, octave_window=9)


def frame_series(x, sr, win, hop):
    """滑窗切片；不足一窗时右侧补零，返回 (frames, 帧起始秒)。"""
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop].copy()
    times = np.arange(len(frames)) * (hop / float(sr))
    return frames, times


def pitch_of(frame, sr, flo, fhi, fmin, fmax, hann):
    """单帧基频估计：返回 (Hz, 置信度, 带通能量dB)；无声返回 (None, conf, 很低)。"""
    x = frame - frame.mean()
    if float(np.sqrt(np.mean(x * x))) < 1e-4:
        return None, 0.0, -200.0
    n = len(x)
    spec = np.fft.rfft(x * hann, 2 * n)
    freqs = np.fft.rfftfreq(2 * n, 1.0 / sr)
    spec = spec * ((freqs >= flo) & (freqs <= fhi))  # 带通：压掉贝斯与高频泛音
    ac = np.fft.irfft(np.abs(spec) ** 2)[:n]
    if ac[0] <= 0:
        return None, 0.0, -200.0
    band_db = 10.0 * math.log10(max(float(ac[0]), 1e-20))  # 带通后能量（用于有无音门限）
    ac = ac / ac[0]
    lag_min = max(1, int(sr / fmax))
    lag_max = min(len(ac) - 2, int(sr / fmin))
    if lag_max <= lag_min:
        return None, 0.0, band_db
    seg = ac[lag_min:lag_max]
    i = int(seg.argmax())
    conf = float(seg[i])
    lag = lag_min + i
    a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
    denom = a - 2 * b + c
    shift = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
    shift = float(max(-1.0, min(1.0, shift)))
    f = float(sr / (lag + shift))
    return (f if fmin <= f <= fmax else None), conf, band_db


def to_midi(f):
    return 69.0 + 12.0 * math.log2(f / 440.0)


def fix_octaves(midis, window):
    """中值平滑 + 单八度纠正：与邻域中值相差 6~18 半音时整体挪一个八度。"""
    out = list(midis)
    idx = [i for i, m in enumerate(midis) if m is not None]
    if len(idx) < 3:
        return out
    half = max(1, window // 2)
    for k, i in enumerate(idx):
        lo, hi = max(0, k - half), min(len(idx), k + half + 1)
        med = float(np.median([midis[idx[j]] for j in range(lo, hi)]))
        d = midis[i] - med
        if 6.0 < abs(d) < 18.0:
            out[i] = midis[i] - 12.0 * math.copysign(1.0, d)
    return out


def merge_notes(frames, times, opts):
    """把帧级音高合并为音符事件：返回 [dict(start,end,dur,note,midi,cents,freq,conf), ...]。"""
    confs = [f["conf"] for f in frames if f["voiced"]]
    ev = []
    cur = None
    for fr, t in zip(frames, times):
        if not fr["voiced"]:
            if cur:
                ev.append(cur)
                cur = None
            continue
        midi = fr["midi"]
        if cur is not None and abs(midi - cur["_midi"]) <= 0.6:
            cur["_list"].append(midi)
            cur["_midi"] = float(np.median(cur["_list"]))
            cur["end"] = t + opts["hop_sec"]
            cur["conf"] = max(cur["conf"], fr["conf"])
        else:
            if cur:
                ev.append(cur)
            cur = {"start": t, "end": t + opts["hop_sec"], "_midi": midi,
                   "_list": [midi], "freq": fr["freq"], "conf": fr["conf"],
                   "voiced": True}
    if cur:
        ev.append(cur)

    merged = []
    for n in ev:
        n["dur"] = n["end"] - n["start"]
        if n["dur"] < opts["min_dur"]:
            continue
        if merged and abs(n["_midi"] - merged[-1]["_midi"]) <= 0.6 \
                and n["start"] - merged[-1]["end"] <= opts["merge_gap"]:
            prev = merged[-1]
            prev["_list"].extend(n["_list"])
            prev["_midi"] = float(np.median(prev["_list"]))
            prev["end"] = n["end"]
            prev["dur"] = prev["end"] - prev["start"]
            prev["conf"] = max(prev["conf"], n["conf"])
        else:
            merged.append(n)

    notes = []
    for n in merged:
        midi = n["_midi"]
        note, cents = freq_to_note(440.0 * 2.0 ** ((midi - 69.0) / 12.0))
        notes.append({"start": round(n["start"], 3), "end": round(n["end"], 3),
                      "dur": round(n["dur"], 3), "note": note, "midi": round(midi, 2),
                      "cents": cents, "freq": round(440.0 * 2.0 ** ((midi - 69.0) / 12.0), 2),
                      "conf": round(n["conf"], 3)})
    return notes, confs


def run(audio, start, end, opts):
    x_full, sr = decode(audio)
    total = len(x_full) / float(sr)
    if start is None:
        start = 0.0
    n0 = max(0, int(round(start * sr)))
    n1 = len(x_full) if end is None else min(len(x_full), int(round(end * sr)))
    if n1 - n0 < opts["win"]:
        print("ERROR: 区间 [%.3fs, %s] 太短，不足一窗（%d 采样）" %
              (start, "末端" if end is None else "%.3fs" % end, opts["win"]), file=sys.stderr)
        return 1
    seg = x_full[n0:n1].astype(np.float64)
    seg_start, seg_end = n0 / float(sr), n1 / float(sr)

    frames_raw, times_rel = frame_series(seg, sr, opts["win"], opts["hop"])
    hann = np.hanning(opts["win"])
    est = [pitch_of(f, sr, opts["band_lo"], opts["band_hi"],
                    opts["fmin"], opts["fmax"], hann) for f in frames_raw]
    loud = [e[2] for e in est if e[2] > -180.0]
    gate = (float(np.percentile(loud, 95)) if loud else -200.0) - opts["db_rel"]
    voiced, midis, confs, freqs = [], [], [], []
    for f0, conf, bdb in est:
        ok = f0 is not None and conf >= opts["min_conf"] and bdb >= gate and bdb > -90.0
        voiced.append(bool(ok))
        midis.append(to_midi(f0) if ok else None)
        confs.append(conf)
        freqs.append(f0)

    midis = fix_octaves(midis, opts["octave_window"])
    frames = [{"t": round(t, 3), "voiced": v, "freq": (round(f, 2) if f else None),
               "midi": (round(m, 2) if m else None), "conf": round(c, 3)}
              for t, v, m, c, f in zip(times_rel, voiced, midis, confs, freqs)]
    inner = [{"voiced": v, "midi": m, "conf": c, "freq": f}
             for v, m, c, f in zip(voiced, midis, confs, freqs)]
    opts2 = dict(opts)
    opts2["hop_sec"] = opts["hop"] / float(sr)
    notes, conf_ok = merge_notes(inner, list(times_rel), opts2)

    offset = seg_start
    for n in notes:
        n["start"] = round(n["start"] + offset, 3)
        n["end"] = round(n["end"] + offset, 3)
    return die_out(audio, sr, seg_start, seg_end, total, notes, frames, opts, conf_ok)


def melody_line(notes, max_chars=200):
    """把音名序列压成一行（超过 max_chars 截断并提示）。"""
    names = [n["note"] for n in notes]
    line = " ".join(names)
    return line if len(line) <= max_chars else line[:max_chars] + " …(共 %d 音)" % len(names)


def die_out(audio, sr, seg_start, seg_end, total, notes, frames, opts, conf_ok):
    print("# 音频 %s  总长 %.2fs  采样率 %d" % (os.path.basename(audio), total, sr))
    print("# 分析区间 %.3f→%.3fs（时长 %.2fs）  窗 %d/帧移 %d  带通 %.0f~%.0fHz" %
          (seg_start, seg_end, seg_end - seg_start, opts["win"], opts["hop"],
           opts["band_lo"], opts["band_hi"]))
    if not notes:
        print("# 未检出可靠音高（可能为纯打击/噪声段，可调 --min-conf / --band-lo）")
        return {"notes": notes, "frames": frames, "segment": [seg_start, seg_end]}
    print("# 检出 %d 个音（置信度均值 %.2f）" %
          (len(notes), sum(n["conf"] for n in notes) / len(notes)))
    print("# 音名序列: " + melody_line(notes))
    print("# 音符表 (start→end  音名  音分  频率Hz  置信度)")
    for n in notes:
        print("  %8.3fs → %8.3fs  %-4s %+4dc  %8.2fHz  %.2f" %
              (n["start"], n["end"], n["note"], n["cents"], n["freq"], n["conf"]))
    return {"notes": notes, "frames": frames, "segment": [seg_start, seg_end]}


def write_files(audio, payload, opts):
    stem = os.path.splitext(os.path.basename(audio))[0]
    out_dir = opts["out_dir"] or os.path.dirname(os.path.abspath(audio))
    notes = payload["notes"]
    jpath = os.path.join(out_dir, stem + "_melody.json")
    cpath = os.path.join(out_dir, stem + "_melody.csv")
    meta = {"source": os.path.abspath(audio), "segment": payload["segment"],
            "params": {k: opts[k] for k in DEFAULTS}, "count": len(notes),
            "notes": notes, "frames": payload["frames"]}
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    with open(cpath, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["start_sec", "end_sec", "dur_sec", "note", "midi", "cents", "freq_hz", "conf"])
        for n in notes:
            w.writerow([n["start"], n["end"], n["dur"], n["note"], n["midi"],
                        n["cents"], n["freq"], n["conf"]])
    print("# 已写出 %s / %s" % (jpath, cpath))


def main(argv=None):
    ap = argparse.ArgumentParser(description="旋律提取：分析指定时间区间，输出音名序列")
    ap.add_argument("audio", help="音频文件路径")
    ap.add_argument("--start", type=float, default=0.0, help="起始秒（默认 0）")
    ap.add_argument("--end", type=float, default=None, help="结束秒（缺省=到音频结尾）")
    ap.add_argument("--win", type=int, default=DEFAULTS["win"], help="分析窗长采样点（默认 2048）")
    ap.add_argument("--hop", type=int, default=DEFAULTS["hop"], help="帧移采样点（默认 256≈11.6ms）")
    ap.add_argument("--band-lo", type=float, default=DEFAULTS["band_lo"], help="带通下限 Hz（默认 100）")
    ap.add_argument("--band-hi", type=float, default=DEFAULTS["band_hi"], help="带通上限 Hz（默认 1800）")
    ap.add_argument("--fmin", type=float, default=DEFAULTS["fmin"], help="基频搜索下限 Hz（默认 80）")
    ap.add_argument("--fmax", type=float, default=DEFAULTS["fmax"], help="基频搜索上限 Hz（默认 1200）")
    ap.add_argument("--min-conf", type=float, default=DEFAULTS["min_conf"], help="自相关置信度门限（默认 0.40）")
    ap.add_argument("--min-dur", type=float, default=DEFAULTS["min_dur"], help="最短音符秒（默认 0.08）")
    ap.add_argument("--merge-gap", type=float, default=DEFAULTS["merge_gap"], help="同音合并间隔秒（默认 0.06）")
    ap.add_argument("--db-rel", type=float, default=DEFAULTS["db_rel"],
                    help="带通能量的相对门限 dB（默认 30，低于峰值该值视为无音）")
    ap.add_argument("--out-dir", default=None, help="输出目录（默认音频所在目录）")
    ap.add_argument("--no-files", action="store_true", help="只打印，不写 json/csv")
    args = ap.parse_args(argv)

    opts = dict(DEFAULTS)
    for k in DEFAULTS:
        v = getattr(args, k, None)
        if v is not None:
            opts[k] = v
    opts["out_dir"] = args.out_dir
    if args.end is not None and args.end <= args.start:
        print("ERROR: --end(%.3fs) 必须大于 --start(%.3fs)" % (args.end, args.start), file=sys.stderr)
        return 1
    try:
        payload = run(args.audio, args.start, args.end, opts)
    except FileNotFoundError as e:
        print("ERROR: 文件不存在: %s" % e, file=sys.stderr)
        return 1
    if isinstance(payload, int):
        return payload
    if not args.no_files and payload["notes"]:
        write_files(args.audio, payload, opts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
