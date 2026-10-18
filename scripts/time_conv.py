#!/usr/bin/env python3
"""秒 ↔ TimeT（拍）换算工具。写谱时的唯一换算入口，禁止手算。

TimeT = [beat, num, den] 表示 beat + num/den 拍（1 拍 = 四分音符 = 60/BPM 秒）。
offset（毫秒，正值 = 谱面相对音乐延后）：beat 0 判定于音乐第 offset/1000 秒。

用法：
  python3 time_conv.py --bpm 128 --offset 59 --to-beat 12.345 [--snap 4]
      音乐第 12.345 秒对应的拍与 TimeT（--snap N 吸附到 1/N 拍，如 4=十六分音符）
  python3 time_conv.py --bpm 128 --offset 59 --to-sec "64,1,4"
      TimeT 对应的音乐秒数
  多 BPM 段：
  python3 time_conv.py --bpm 128 --bpm-segment "96/0/1:150" --offset 0 --to-beat 60.0
"""
import argparse
import re
import sys
from fractions import Fraction


def build_segments(args):
    segs = [{"beat": Fraction(0), "bpm": args.bpm}]
    for text in args.bpm_segment:
        m = re.fullmatch(r"(\d+)/(\d+)/(\d+):([\d.]+)", text.strip())
        if not m:
            print('ERROR: --bpm-segment 格式应为 "拍/分子/分母:BPM"，如 "96/0/1:150"', file=sys.stderr)
            sys.exit(1)
        segs.append({"beat": Fraction(int(m.group(1)) + int(m.group(2)), int(m.group(3))),
                     "bpm": float(m.group(4))})
    segs.sort(key=lambda s: s["beat"])
    if segs[0]["beat"] != 0:
        print("ERROR: 第一段 BPM 必须从拍 0 开始", file=sys.stderr)
        sys.exit(1)
    for a, b in zip(segs, segs[1:]):
        if a["beat"] >= b["beat"]:
            print("ERROR: BPM 段起始拍必须严格递增", file=sys.stderr)
            sys.exit(1)
    # 累计每段起始秒
    sec = args.offset / 1000.0
    for i, s in enumerate(segs):
        s["sec"] = sec
        if i + 1 < len(segs):
            sec += float(segs[i + 1]["beat"] - s["beat"]) * 60.0 / s["bpm"]
    return segs


def beat_to_sec(segs, b):
    b = Fraction(b)
    if b < 0:
        print(f"WARNING: 拍 {float(b):.4f} 为负（在 beat 0 之前）", file=sys.stderr)
    seg = segs[0]
    for s in segs:
        if s["beat"] <= b:
            seg = s
        else:
            break
    return seg["sec"] + float(b - seg["beat"]) * 60.0 / seg["bpm"]


def sec_to_beat(segs, t):
    seg = segs[0]
    for s in segs:
        if s["sec"] <= t:
            seg = s
        else:
            break
    if t < segs[0]["sec"]:
        print(f"WARNING: {t}s 早于 beat 0（{segs[0]['sec']}s），拍为负", file=sys.stderr)
    return float(seg["beat"]) + (t - seg["sec"]) * seg["bpm"] / 60.0


def split_time_t(b, snap):
    """把拍数拆成 [整拍, 分子, 分母]，分数部分吸附到 1/snap 的整数倍。"""
    neg = b < 0
    b = abs(b)
    whole = int(b)
    frac = b - whole
    if snap and snap > 0:
        num = round(frac * snap)
        den = snap
        if num == den:
            whole += 1
            num = 0
        if num == 0:
            den = 1
        elif num > 0:
            from math import gcd
            g = gcd(num, den)
            num, den = num // g, den // g
    else:
        f = Fraction(frac).limit_denominator(960)
        num, den = f.numerator, f.denominator
        if num == 0:
            den = 1
    if neg:
        print("WARNING: 负拍已按绝对值拆分，请人工确认", file=sys.stderr)
    return [whole, num, den]


def main(argv=None):
    ap = argparse.ArgumentParser(description="秒 ↔ TimeT 换算（写谱专用，勿手算）")
    ap.add_argument("--bpm", type=float, required=True, help="首段 BPM")
    ap.add_argument("--bpm-segment", action="append", default=[],
                    metavar='"拍/分子/分母:BPM"', help="追加 BPM 变化段，可多次")
    ap.add_argument("--offset", type=float, default=0.0, help="META.offset 毫秒（默认 0）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--to-beat", type=float, metavar="秒", help="音乐秒数 → 拍/TimeT")
    g.add_argument("--to-sec", type=str, metavar="拍,分子,分母", help="TimeT → 音乐秒数")
    ap.add_argument("--snap", type=int, default=0,
                    help="吸附分母（拍的小数部分取 1/N 拍），如 2=八分 4=十六分 3=三连；0=精确")
    args = ap.parse_args(argv)

    segs = build_segments(args)
    if args.to_beat is not None:
        b = sec_to_beat(segs, args.to_beat)
        t = split_time_t(b, args.snap)
        # 用吸附结果回验，给出偏差
        back = beat_to_sec(segs, t[0] + t[1] / t[2])
        print(f"音乐 {args.to_beat}s -> 精确拍 {b:.5f} -> TimeT {t} "
              f"(= {t[0] + t[1]/t[2]:.5f} 拍, 回验 {back:.5f}s, 偏差 {abs(back-args.to_beat)*1000:.1f}ms)")
    else:
        parts = re.fullmatch(r"(-?\d+)\s*,\s*(\d+)\s*,\s*(\d+)", args.to_sec.strip())
        if not parts:
            print('ERROR: --to-sec 格式应为 "拍,分子,分母"，如 "64,1,4"', file=sys.stderr)
            return 1
        b = int(parts.group(1)) + int(parts.group(2)) / int(parts.group(3))
        s = beat_to_sec(segs, b)
        print(f"TimeT [{parts.group(1)}, {parts.group(2)}, {parts.group(3)}] "
              f"(= {b:.5f} 拍) -> 音乐 {s:.5f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
