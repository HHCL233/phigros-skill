#!/usr/bin/env python3
"""按「区间 + 首音 x + 几分 + 配置编号」生成 RPE 音符配置，输出可直接写入谱面的 notes。

生成的是配置骨架：坐标/拍位/类型按配置库（configs.md A~G）的规则铺开，
落点与真实音乐无关——**必须再按采音结果逐音微调**（调 x、挪拍位、换类型、镜像、增删），
不得把生成结果原样交付。

用法：
  python3 gen_config.py --config A2 --start 16/0/1 --end 24/0/1 --x -337.5 --div 16
      # 生成 16 拍起、24 拍止（不含 24 拍）的对称双押；输出 notes JSON 数组
  python3 gen_config.py --config D1 --start 8/0/1 --end 8/3/4 --x 405 --div 8 --direction down
  python3 gen_config.py --config E2 --start 32/0/1 --end 36/0/1 --x -270 --div 4
  python3 gen_config.py --config A4 --start 64/0/1 --end 65/0/1 --x 0 --div 4 --stack 3

脚本只负责生成 notes JSON，不负责把它写进谱面——由 AI 按需挑选、微调后自行写入目标判定线。

参数：
  --start/--end  区间，TimeT 拍/分子/分母（第一位是拍，不是小节），如 16/0/1；start < end
  --x            第一个 note 的 positionX（像素，|x|≤675）
  --div          几分音符 = 相邻音的拍间距 step=4/div（四分=1 拍）：4→1、8→1/2、16→1/4、32→1/8、
                 64→1/16；三连：12→1/3（三连八分）、24→1/6（三连十六分）、48→1/12
  --config       配置编号（A~G 见 configs.md 配置库）；H 为结构手法，不是键型配置
  --stack        叠判定/散点押数（A4/A5，2~4，默认 2/3）
  --spread       A8 宽幅间距（默认 300）
  --hold         E1 Hold 时长（拍，默认 0.5）；E4 固定 1/16 拍拖尾
  --direction    up/down（楼梯/同侧双押的走向，默认 down）
  --seed         B2 不规则交互的伪随机种子（默认 0，同种子结果可复现）
  --above        音符 above（1 上 / 0,2 下）；生成的音符一律 isFake=0（假音符已全面禁用）
  --out          把 notes JSON 写入文件（不写则打印到 stdout）

各配置的生成规则（几何骨架，参数可在给定范围内缩放后微调）：
  A1 单点流          | 每步 1 个 Tap，x 不变
  A2 对称双押        | 每步 2 个 Tap，x 与 -x
  A3 中侧双押        | 每步 2 个 Tap，x 与 0
  A4 同位叠判定      | 每步同刻同 x 叠 --stack 个不同类型（4 叠含 1/16 拍微 Hold；
                     | 同位叠判定是合法手法，校验器的「同刻间距 ≤50」WARNING 可忽略）
  A5 散点 3~4 押     | 每步在 x 邻域铺 --stack 个不同 x（135 网格展开）
  A6 同侧下行双押    | 每步 2 个 Tap，沿 52.5 网格整体步进
  A7 八分双押海      | 同 A2 几何（连续双押，单段 ≤4 组即收，勿滥用）
  A8 非对称宽幅双押  | 每步 2 个 Tap，间距 --spread（默认 300）
  B1 对称等间隔交互  | 每步 1 个 Tap，x 与 -x 交替
  B2 不规则交互      | 每步 1 个 Tap，x 在常用网格内伪随机（--seed）
  B3 对称展开交互    | 每步 1 个 Tap，x↔-x 逐步向外展开
  B4 中侧穿插交互    | 每步 1 个 Tap，循环 [x, 0, -x, 0]
  C1 中线长纵连      | 同 A1 几何（同 x 连续 Tap，单段 ≤6 连即收）
  C2 混合纵连        | 每步 1 个，同 x，类型循环 T,T,T,T,F,D
  C3 短纵连收尾      | 同 x 连续 Tap，末音接 Flick
  D1 T→D 楼梯        | 首音 Tap，其后 Drag 沿 67.5 网格步进
  D2 F→D 楼梯        | 首音 Flick，其后 Drag 沿 135 网格步进
  D3 纯 Drag 楼梯    | 全 Drag，沿 67.5 网格步进
  D4 H 起手楼梯      | 首音 Hold（1 步长），接 Tap 后 Drag 沿 150 网格步进
  D5 楼梯接粉        | Drag 楼梯（67.5），末音改 Flick 收尾
  E1 短 Hold         | 每步 1 个 Hold，时长 --hold 拍
  E2 Hold 铺底叠点   | 1 个长 Hold 覆盖区间，期间对侧 -x 每步 1 个 Tap
  E3 双侧 Hold+Drag  | ±x 两条长 Hold 覆盖区间，x=0 每步 1 个 Drag
  E4 装饰微 Hold     | 每步 1 个 1/16 拍微 Hold（视觉拖尾）
  F1 16 分 Drag 链   | 每步 1 个 Drag，x 不变
  F2 32/64 分极速链  | 同 F1 几何（把 --div 调细，爆发段专用）
  F3 Drag 双押海     | 每步 2 个 Drag，x 与 -x
  F4 短 Drag 链点缀  | 每 3 个 Drag 后接 1 个 Tap 呼吸（链间不断长链）
  G1 粉接八          | Tap 与 Flick 逐步交替（间隔 ≥1/4 拍）
  G2 Flick 链        | 每步 1 个 Flick
  G3 中线重音 Flick  | 区间起点 1 个 Flick（强拍点睛）
"""
import argparse
import json
import math
import random
import re
import sys
from fractions import Fraction

GRID = 67.5
X_LIMIT = 675.0
X_WARN = 50.0  # 手感红线：同刻/同期同线间距必须 > 50


def parse_timet(text):
    m = re.fullmatch(r"(-?\d+)\s*/\s*(\d+)\s*/\s*(\d+)", text.strip())
    if not m:
        raise argparse.ArgumentTypeError('时间格式应为 "拍/分子/分母"，如 16/0/1')
    beat, num, den = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if den == 0 or num < 0 or num >= den:
        raise argparse.ArgumentTypeError('时间分母须 >0 且 0 ≤ 分子 < 分母，如 16/1/4')
    return Fraction(beat) + Fraction(num, den)


def to_timet(fr):
    fr = Fraction(fr)
    whole = math.floor(fr) if fr >= 0 else -math.ceil(-fr)
    frac = fr - whole
    if frac == 0:
        return [whole, 0, 1]
    if frac.denominator > 960:
        frac = frac.limit_denominator(960)
        if frac == 1:
            whole, frac = whole + 1, Fraction(0)
    return [whole, frac.numerator, frac.denominator]


def note(ntype, t, x, end=None, above=1):
    return {
        "type": ntype,
        "startTime": to_timet(t),
        "endTime": to_timet(end if end is not None else t),
        "positionX": round(float(x), 4),
        "above": above,
        "speed": 1.0,
        "yOffset": 0.0,
        "isFake": 0,
        "size": 1.0,
        "visibleTime": 999999.0,
        "alpha": 255,
    }


def times_of(start, end, step):
    ts, t = [], start
    while t < end:
        ts.append(t)
        t += step
        if len(ts) > 5000:
            print("ERROR: 生成音符数超过 5000，请检查区间/--div", file=sys.stderr)
            sys.exit(1)
    return ts


def note_beat(n):
    t = n["startTime"]
    return t[0] + t[1] / t[2]


def sort_notes(notes):
    def key(n):
        return (1 if n.get("type") == 2 else 0, note_beat(n), n.get("positionX", 0.0))
    return sorted(notes, key=key)


def build(cfg, x, start, end, step, o):
    """返回 (notes, 说明)。"""
    ts = times_of(start, end, step)
    if not ts:
        print("ERROR: 区间内没有音符（start 必须早于 end）", file=sys.stderr)
        sys.exit(1)
    N = []
    d = -1 if o.direction == "down" else 1
    if cfg in ("A1", "C1"):
        N = [note(1, t, x) for t in ts]
    elif cfg in ("A2", "A7"):
        for t in ts:
            N += [note(1, t, x), note(1, t, -x)]
        if cfg == "A7":
            return _finish(N, o, "连续双押铺开（单段 ≤4 组即收）")
        if abs(2 * x) <= X_WARN:
            print("WARNING: A2 双侧 x=%.1f 与 -x 间距 %g ≤50，违反同刻间距红线" % (x, abs(2 * x)), file=sys.stderr)
    elif cfg == "A3":
        N = [n for t in ts for n in (note(1, t, x), note(1, t, 0.0))]
        if abs(x) <= X_WARN:
            print("WARNING: A3 中侧 x=%.1f 与 0 间距 ≤50，违反同刻间距红线" % x, file=sys.stderr)
    elif cfg == "A4":
        n = max(2, min(4, o.stack))
        kinds = (1, 3, 4, 2)  # 同刻同 x 用不同类型叠判定；n=4 的 Hold 给 1/16 拍微时长
        for t in ts:
            for k in range(n):
                end = t + Fraction(1, 16) if kinds[k] == 2 else None
                N.append(note(kinds[k], t, x, end=end))
        return _finish(N, o, "同刻同 x 叠 %d 判定（单点全吃，押数只算 1）" % n)
    elif cfg == "A5":
        n = max(3, min(4, o.stack if o.stack >= 3 else 3))
        offs = {3: (0, GRID * 2, -GRID * 2), 4: (0, GRID * 2, -GRID * 2, GRID * 4)}[n]
        for t in ts:
            for off in offs:
                pos = max(-X_LIMIT, min(X_LIMIT, x + off))
                N.append(note(1, t, pos))
        return _finish(N, o, "%d 押散点（仅高潮段少量使用）" % n)
    elif cfg == "A6":
        for i, t in enumerate(ts):
            a = max(-X_LIMIT, min(X_LIMIT, x + d * 52.5 * i))
            N += [note(1, t, a), note(1, t, min(X_LIMIT, a + 52.5))]
        return _finish(N, o, "同侧双押沿 52.5 网格步进")
    elif cfg == "A8":
        sp = o.spread
        for t in ts:
            N += [note(1, t, x), note(1, t, max(-X_LIMIT, min(X_LIMIT, x + sp)))]
    elif cfg == "B1":
        N = [note(1, t, x if i % 2 == 0 else -x) for i, t in enumerate(ts)]
    elif cfg == "B2":
        grid = [0.0, 67.5, -67.5, 135.0, -135.0, 202.5, -202.5, 270.0, -270.0, 337.5, -337.5, 405.0, -405.0]
        rng = random.Random(o.seed)
        N = [note(1, t, rng.choice(grid)) for t in ts]
        return _finish(N, o, "不规则交互（排键整齐优先，仅该风格时用）")
    elif cfg == "B3":
        seq = []
        for k in range(1, 6):
            seq += [k * abs(x) if x else GRID * 2 * k, -(k * abs(x) if x else GRID * 2 * k)]
        N = []
        for i, t in enumerate(ts):
            pos = max(-X_LIMIT, min(X_LIMIT, seq[i % len(seq)] * (1 if x >= 0 else -1)))
            N.append(note(1, t, pos))
    elif cfg == "B4":
        pat = (x, 0.0, -x, 0.0)
        N = [note(1, t, pat[i % 4]) for i, t in enumerate(ts)]
        if abs(x) <= X_WARN:
            print("WARNING: B4 侧位 %.1f 与 0 间距 ≤50，违反同刻/相邻间距红线" % x, file=sys.stderr)
    elif cfg == "C2":
        types = (1, 1, 1, 1, 3, 4)
        N = [note(types[i % 6], t, x) for i, t in enumerate(ts)]
    elif cfg == "C3":
        N = [note(1, t, x) for t in ts[:-1]] + [note(3, ts[-1], x)]
    elif cfg in ("D1", "D2", "D3"):
        first, g = {"D1": (1, GRID), "D2": (3, 135.0), "D3": (4, GRID)}[cfg]
        for i, t in enumerate(ts):
            pos = max(-X_LIMIT, min(X_LIMIT, x + d * g * i))
            N.append(note(first if i == 0 else 4, t, pos))
    elif cfg == "D4":
        N = [note(2, ts[0], x, end=ts[0] + step)]
        for i, t in enumerate(ts[1:]):
            pos = max(-X_LIMIT, min(X_LIMIT, x + d * 150.0 * i))
            N.append(note(1 if i == 0 else 4, t, pos))
    elif cfg == "D5":
        for i, t in enumerate(ts):
            pos = max(-X_LIMIT, min(X_LIMIT, x + d * GRID * i))
            N.append(note(3 if i == len(ts) - 1 else 4, t, pos))
    elif cfg == "E1":
        hold = o.hold
        N = [note(2, t, x, end=t + hold) for t in ts]
    elif cfg == "E2":
        N = [note(2, ts[0], x, end=end)]
        if abs(x) <= X_WARN:
            print("WARNING: E2 主 Hold x=%.1f 与对侧叠点间距 ≤50，违反长条期间间距红线" % x, file=sys.stderr)
        N += [note(1, t, -x) for t in ts]
        return _finish(N, o, "长 Hold 铺底 + 对侧叠点")
    elif cfg == "E3":
        N = [note(2, ts[0], x, end=end), note(2, ts[0], -x, end=end)]
        if abs(x) <= X_WARN:
            print("WARNING: E3 双侧 Hold x=%.1f 间距 ≤50，违反同刻间距红线" % x, file=sys.stderr)
        N += [note(4, t, 0.0) for t in ts]
        return _finish(N, o, "双侧 Hold 铺底 + 中线 Drag 海")
    elif cfg == "E4":
        N = [note(2, t, x, end=t + Fraction(1, 16)) for t in ts]
    elif cfg == "F1":
        N = [note(4, t, x) for t in ts]
    elif cfg == "F2":
        N = [note(4, t, x) for t in ts]
        return _finish(N, o, "极速 Drag 链（把 --div 调细到 32/64 用）")
    elif cfg == "F3":
        N = [n for t in ts for n in (note(4, t, x), note(4, t, -x))]
    elif cfg == "F4":
        for i, t in enumerate(ts):
            N.append(note(1, t, x) if (i + 1) % 4 == 0 else note(4, t, x))
        return _finish(N, o, "每 3 个 Drag 后接 1 个 Tap 呼吸")
    elif cfg == "G1":
        N = [note(1 if i % 2 == 0 else 3, t, x) for i, t in enumerate(ts)]
        if step < Fraction(1, 4):
            print("WARNING: G1 间隔 %s 拍 <1/4 拍，粉接八建议 ≥1/4 拍" % step, file=sys.stderr)
    elif cfg == "G2":
        N = [note(3, t, x) for t in ts]
    elif cfg == "G3":
        N = [note(3, start, x)]
        return _finish(N, o, "强拍单 Flick 点睛")
    else:
        print("ERROR: 不支持的配置编号 %s（支持 A1~G3；H 为结构手法，请改用对应键型配置）" % cfg,
              file=sys.stderr)
        sys.exit(1)
    return _finish(N, o, "")


def _finish(notes, o, extra):
    for n in notes:
        n["above"] = o.above
    return sort_notes(notes), extra


DIV_MAP = {4: "四分", 8: "八分", 12: "三连八分", 16: "十六分", 24: "三连十六分（24 分）",
           32: "三十二分", 48: "三连三十二分", 64: "六十四分"}


def main(argv=None):
    ap = argparse.ArgumentParser(description="按区间/首音 x/几分/配置编号生成 RPE 音符配置")
    ap.add_argument("--config", required=True, help="配置编号（A1~G3，见 configs.md 配置库）")
    ap.add_argument("--start", required=True, type=parse_timet, help='起始 TimeT，如 16/0/1')
    ap.add_argument("--end", required=True, type=parse_timet, help='结束 TimeT（不含），如 24/0/1')
    ap.add_argument("--x", required=True, type=float, help="第一个 note 的 positionX")
    ap.add_argument("--div", type=int, default=16, help="几分音符（4/8/16/24/32/64…，默认 16）")
    ap.add_argument("--stack", type=int, default=2, help="A4/A5 押数（2~4，A5 默认 3）")
    ap.add_argument("--spread", type=float, default=300.0, help="A8 宽幅间距（默认 300）")
    ap.add_argument("--hold", type=float, default=0.5, help="E1 Hold 时长（拍，默认 0.5）")
    ap.add_argument("--direction", choices=("up", "down"), default="down", help="楼梯/同侧双押走向")
    ap.add_argument("--seed", type=int, default=0, help="B2 伪随机种子（默认 0）")
    ap.add_argument("--above", type=int, choices=(0, 1, 2), default=1, help="音符 above（1 上 / 0,2 下）")
    ap.add_argument("--out", default=None, help="把 notes JSON 写入该文件（默认打印到 stdout）")
    args = ap.parse_args(argv)

    if args.div <= 0:
        print("ERROR: --div 必须为正整数", file=sys.stderr)
        return 1
    if args.start >= args.end:
        print("ERROR: --start 必须早于 --end", file=sys.stderr)
        return 1
    if abs(args.x) > X_LIMIT:
        print("WARNING: --x=%.1f 超出 [-675,675]" % args.x, file=sys.stderr)

    cfg = args.config.upper()
    step = Fraction(4, args.div)  # 几分音符的拍长：四分=1 拍
    notes, extra = build(cfg, args.x, args.start, args.end, step, args)
    counts = {}
    for n in notes:
        counts[n["type"]] = counts.get(n["type"], 0) + 1
    span = "%s~%s" % (str(to_timet(args.start)), str(to_timet(args.end)))
    print("配置 %s（%s）%s：%d 个音符，拍区间 %s，分音 %d"
          % (cfg, DIV_MAP.get(args.div, "%d 分" % args.div), ("· " + extra) if extra else "",
             len(notes), span, args.div), file=sys.stderr)
    print("类型 T/F/D/H = %d/%d/%d/%d"
          % (counts.get(1, 0), counts.get(3, 0), counts.get(4, 0), counts.get(2, 0)), file=sys.stderr)

    text = json.dumps(notes, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print("已写入 %s（%d 个音符）" % (args.out, len(notes)), file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
