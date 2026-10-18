#!/usr/bin/env python3
"""生成 RPE 谱面骨架（chart JSON + info.txt 模板）。

用法示例：
  python3 scaffold.py --name "Song" --song "song.mp3" --background "cover.jpg" \
      --id 12345678 --level "IN Lv.14" --composer "Composer" --charter "AI" \
      --bpm 170 --out-dir ./out

  # 变 BPM 段：
  python3 scaffold.py ... --bpm 128 --bpm-segment "96/0/1:150"
  （格式为 拍/分子/分母:BPM，TimeT 第一位是拍不是小节；startTime 必须晚于前一段）
"""
import argparse
import json
import os
import re
import sys

DEFAULT_LINE_TEXTURE = "line.png"


def control_pair(key, value):
    return [
        {"easing": 1, "x": 0.0, key: value},
        {"easing": 1, "x": 9999999.0, key: value},
    ]


def default_alpha_event(alpha=255.0):
    """恒定 alpha 覆盖全曲：skill 要求每条判定线至少一条 alphaEvents（0~255）。
    音符线用 255；表演线（无可判音符）不得超过 200（skill 规则）。"""
    return {
        "bezier": 0,
        "bezierPoints": [0.0, 0.0, 0.0, 0.0],
        "easingLeft": 0.0,
        "easingRight": 1.0,
        "easingType": 1,
        "start": alpha,
        "startTime": [0, 0, 1],
        "end": alpha,
        "endTime": [1000, 0, 1],
        "linkgroup": 0,
    }


def empty_event_layer(alpha=255.0):
    return {
        "moveXEvents": [],
        "moveYEvents": [],
        "rotateEvents": [],
        "alphaEvents": [default_alpha_event(alpha)],
        "speedEvents": [],
    }


def default_incline_event():
    return {
        "bezier": 0,
        "bezierPoints": [0.0, 0.0, 0.0, 0.0],
        "easingLeft": 0.0,
        "easingRight": 1.0,
        "easingType": 0,
        "end": 0.0,
        "endTime": [1, 0, 1],
        "linkgroup": 0,
        "start": 0.0,
        "startTime": [0, 0, 1],
    }


def default_judge_line(group=0, alpha=255.0):
    return {
        "Group": group,
        "Name": "Untitled",
        "Texture": DEFAULT_LINE_TEXTURE,
        "alphaControl": control_pair("alpha", 1.0),
        "anchor": [0.5, 0.5],
        "bpmfactor": 1.0,
        "eventLayers": [empty_event_layer(alpha)],
        "extended": {"inclineEvents": [default_incline_event()]},
        "father": -1,
        "isCover": 1,
        "isGif": False,
        "notes": [],
        "numOfNotes": 0,
        "posControl": control_pair("pos", 1.0),
        "sizeControl": control_pair("size", 1.0),
        "skewControl": control_pair("skew", 0.0),
        "yControl": control_pair("y", 1.0),
        "zOrder": 0,
    }


def parse_time_tuple(text):
    m = re.fullmatch(r"(-?\d+)/(\d+)/(\d+)", text.strip())
    if not m:
        raise argparse.ArgumentTypeError(
            "时间格式应为 拍/分子/分母（TimeT 第一位是拍，不是小节），如 96/0/1"
        )
    return [int(m.group(1)), int(m.group(2)), int(m.group(3))]


def beat(t):
    return t[0] + t[1] / t[2]


def parse_bpm_segment(text):
    time_part, _, bpm_part = text.rpartition(":")
    if not time_part or not bpm_part:
        raise argparse.ArgumentTypeError('BPM 段格式应为 "拍/分子/分母:BPM"，如 "96/0/1:150"')
    return {"bpm": float(bpm_part), "startTime": parse_time_tuple(time_part)}


def sanitize_filename(name, fallback="chart"):
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return name or fallback


def build_info_txt(meta):
    lines = [
        "#",
        f"Name: {meta['name']}",
        f"Path: {meta['id']}",
        f"Song: {meta['song']}",
        f"Picture: {meta['background']}",
        f"Chart: {meta['chart_file']}",
        f"Level: {meta['level']}",
        f"Composer: {meta['composer']}",
        f"Charter: {meta['charter']}",
    ]
    if meta.get("illustration"):
        lines.append(f"Illustration: {meta['illustration']}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成 RPE 谱面骨架")
    ap.add_argument("--name", required=True, help="谱面名（曲名）")
    ap.add_argument("--song", required=True, help="音频文件名（与实际文件一致）")
    ap.add_argument("--background", required=True, help="背景/封面文件名")
    ap.add_argument("--id", required=True, help="谱面 ID")
    ap.add_argument("--level", default="IN Lv.15", help='难度，如 "IN Lv.15"')
    ap.add_argument("--composer", default="Unknown", help="曲师")
    ap.add_argument("--charter", default="AI", help="谱师")
    ap.add_argument("--illustration", default="", help="画师（可选）")
    ap.add_argument("--offset", type=int, default=0, help="谱面偏移（毫秒）")
    ap.add_argument("--bpm", type=float, default=170.0, help="首段 BPM")
    ap.add_argument(
        "--bpm-segment",
        action="append",
        default=[],
        type=parse_bpm_segment,
        metavar='"拍/分子/分母:BPM"',
        help="追加 BPM 变化段，可多次使用",
    )
    ap.add_argument("--rpe-version", type=int, default=150, help="RPEVersion（默认 150）")
    ap.add_argument("--duration", type=float, default=None, help="音频时长秒（可选）")
    ap.add_argument("--chart-file", default=None, help="谱面 JSON 文件名（默认按曲名生成）")
    ap.add_argument(
        "--lines", type=int, default=6,
        help="判定线数量（默认 6 = 1 主音符线 + 5 表演线；表演线无上限，最少 5 条）",
    )
    ap.add_argument("--out-dir", required=True, help="输出目录")
    ap.add_argument("--no-info-txt", action="store_true", help="不生成 info.txt")
    args = ap.parse_args(argv)

    bpm_list = [{"bpm": args.bpm, "startTime": [0, 0, 1]}]
    for seg in args.bpm_segment:
        bpm_list.append(seg)
    bpm_list.sort(key=lambda s: beat(s["startTime"]))
    for prev, cur in zip(bpm_list, bpm_list[1:]):
        if beat(prev["startTime"]) >= beat(cur["startTime"]):
            print("ERROR: BPM 段 startTime 必须严格递增: %s" % bpm_list, file=sys.stderr)
            return 1

    chart_file = args.chart_file or sanitize_filename(args.name) + ".json"

    meta = {
        "RPEVersion": args.rpe_version,
        "name": args.name,
        "song": args.song,
        "background": args.background,
        "id": str(args.id),
        "level": args.level,
        "composer": args.composer,
        "charter": args.charter,
        "offset": args.offset,
    }
    if args.duration is not None:
        meta["duration"] = args.duration
    if args.illustration:
        meta["illustration"] = args.illustration

    chart = {
        "BPMList": bpm_list,
        "META": meta,
        "judgeLineGroup": ["Default"],
        "judgeLineList": [
            default_judge_line(0) if i == 0 else default_judge_line(0, alpha=200.0)
            for i in range(max(1, args.lines))
        ],
        "multiScale": 1.0,
        "multiLineString": "",
    }

    os.makedirs(args.out_dir, exist_ok=True)
    chart_path = os.path.join(args.out_dir, chart_file)
    with open(chart_path, "w", encoding="utf-8") as f:
        json.dump(chart, f, ensure_ascii=False, indent=3)

    if not args.no_info_txt:
        info = build_info_txt({**meta, "chart_file": chart_file})
        with open(os.path.join(args.out_dir, "info.txt"), "w", encoding="utf-8") as f:
            f.write(info)

    print("已生成: %s" % chart_path)
    print("BPM 段: %s" % ", ".join(
        "%s=%g" % ("/".join(map(str, s["startTime"])), s["bpm"]) for s in bpm_list))
    if not args.no_info_txt:
        print("已生成: %s" % os.path.join(args.out_dir, "info.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
