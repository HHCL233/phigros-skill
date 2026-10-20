#!/usr/bin/env python3
"""把谱面目录打包为 .pez（zip）。

用法：python3 package_pez.py <谱面目录> [-o 输出.pez]

目录内必须包含 info.txt，且每个谱面块引用的 Song/Picture/Chart 文件都要存在。
打包前复核谱面音符不晚于音频总时长（读不到音频时长时跳过）。
"""
import argparse
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validate_chart as vc  # noqa: E402  （同目录脚本，复用音频时长与越界检查）


def parse_info(path):
    """解析 info.txt，返回 [(字段名, 值), ...] 列表（按块顺序）。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line == "#":
            continue
        m = re.fullmatch(r"([A-Za-z]+)\s*:\s*(.*)", line)
        if not m:
            print(f"WARNING: info.txt 中无法解析的行: {line!r}")
            continue
        entries.append((m.group(1), m.group(2)))
    return entries


def main(argv=None):
    ap = argparse.ArgumentParser(description="打包谱面目录为 .pez")
    ap.add_argument("directory", help="谱面目录（含 info.txt）")
    ap.add_argument("-o", "--output", default=None, help="输出 .pez 路径（默认 目录名.pez）")
    args = ap.parse_args(argv)

    d = args.directory
    info_path = os.path.join(d, "info.txt")
    if not os.path.isfile(info_path):
        print("ERROR: 目录内缺少 info.txt", file=sys.stderr)
        return 1

    entries = parse_info(info_path)
    if not entries:
        print("ERROR: info.txt 为空", file=sys.stderr)
        return 1

    files = {"info.txt"}
    for key, value in entries:
        if key in ("Song", "Picture", "Chart"):
            p = os.path.join(d, value)
            if not os.path.isfile(p):
                print(f"ERROR: info.txt 引用的 {key} 文件不存在: {value}", file=sys.stderr)
                return 1
            files.add(value)

    songs = [v for k, v in entries if k == "Song"]
    duration = vc.audio_duration_sec(os.path.join(d, songs[0])) if songs else None
    if songs and duration is None:
        print("WARNING: 无法读取音频时长，跳过音符越界检查（需 ffprobe/ffmpeg；WAV 可直读）",
              file=sys.stderr)
    if duration:
        for value in [v for k, v in entries if k == "Chart"]:
            try:
                with open(os.path.join(d, value), encoding="utf-8") as f:
                    chart = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                print(f"ERROR: 无法读取谱面 {value}: {e}", file=sys.stderr)
                return 1
            msgs = vc.notes_beyond_audio(chart, duration)
            if msgs:
                for m in msgs:
                    print(f"ERROR: {value}: {m}", file=sys.stderr)
                return 1

    out = args.output or os.path.basename(os.path.normpath(d)) + ".pez"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            z.write(os.path.join(d, name), arcname=name)

    print("已打包: %s（含 %d 个文件: %s）" % (out, len(files), ", ".join(sorted(files))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
