#!/usr/bin/env python3
"""校验 RPE JSON 谱面。

用法：python3 validate_chart.py <chart.json> [--quiet-warnings]
ERROR 全部修复才算可用；WARNING 酌情处理。退出码 0=无 ERROR，1=有 ERROR。
"""
import argparse
import json
import math
import sys

NOTE_TYPES = {1, 2, 3, 4}
NORMAL_EVENT_KINDS = ("moveXEvents", "moveYEvents", "rotateEvents", "alphaEvents")

errors = []
warnings = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_timet(t, ctx, require_nonneg=True):
    if not (isinstance(t, list) and len(t) == 3 and all(is_num(x) for x in t)):
        err(f"{ctx}: TimeT 应为 [拍, 分子, 分母] 三元组（第一位是拍，不是小节），实际 {t!r}")
        return False
    measure, num, den = t
    if den == 0:
        err(f"{ctx}: 分母不能为 0: {t!r}")
        return False
    if den < 0:
        err(f"{ctx}: 分母应为正数: {t!r}")
        return False
    if num < 0:
        err(f"{ctx}: 分子不应为负: {t!r}")
        return False
    if num >= den:
        warn(f"{ctx}: 分子 {num} >= 分母 {den}（可化简但合法）: {t!r}")
    if require_nonneg and measure < 0:
        err(f"{ctx}: 整拍不应为负: {t!r}")
        return False
    return True


def beat(t):
    return t[0] + t[1] / t[2]


def check_ascending(events, ctx, kind):
    prev = None
    for e in events:
        t = e.get("startTime")
        if not isinstance(t, list) or len(t) != 3:
            continue
        if prev is not None:
            b = beat(t)
            if b < prev:
                err(f"{ctx}: {kind} 事件未按时间升序（{prev:.4f} -> {b:.4f}）")
            elif b == prev:
                warn(f"{ctx}: {kind} 存在相同 startTime 的事件 @{t!r}")
        prev = beat(t)


def check_control_array(arr, key, ctx):
    name = f"{ctx}.{key}"
    if not isinstance(arr, list) or not arr:
        err(f"{name}: 应为非空数组")
        return
    if len(arr) < 2:
        warn(f"{name}: 通常应有两个控制点（起点+终点）")
    for c in arr:
        if not isinstance(c, dict) or not is_num(c.get("x")) or not is_num(c.get(key)) \
                or not isinstance(c.get("easing"), int):
            err(f"{name}: 控制点格式错误，应含 easing(int)/x(number)/{key}(number): {c!r}")
            return


def check_normal_event(e, ctx, kind, extended=False):
    if not isinstance(e, dict):
        err(f"{ctx}: {kind} 事件应为对象: {e!r}")
        return
    for k in ("startTime", "endTime", "start", "end", "easingType",
              "easingLeft", "easingRight", "bezier", "bezierPoints", "linkgroup"):
        if k not in e:
            err(f"{ctx}: {kind} 缺少字段 {k}: {e!r}")
            return
    check_timet(e["startTime"], f"{ctx}.{kind}.startTime")
    check_timet(e["endTime"], f"{ctx}.{kind}.endTime")
    if not is_num(e["start"]) or not is_num(e["end"]):
        err(f"{ctx}: {kind} start/end 应为数值: {e!r}")
    et = e["easingType"]
    if not isinstance(et, int):
        err(f"{ctx}: {kind} easingType 应为整数: {et!r}")
    elif not (0 <= et <= 49):
        # 真实谱面 incline 占位事件的 easingType 随线号递增可远超 49，
        # 属编辑器行为且 start=end 时值恒定不生效，故 extended 事件降为 WARNING。
        msg = (f"{ctx}: {kind} easingType={et} 超出 0~49"
               + ("（extended 占位事件，编辑器行为，可忽略）" if extended else ""))
        (warn if extended else err)(msg)
    elif et > 29:
        warn(f"{ctx}: {kind} easingType={et} 为 RPE 1.5+ 扩展缓动，低版本播放器可能不支持")
    l, r = e["easingLeft"], e["easingRight"]
    if not (is_num(l) and is_num(r) and 0 <= l <= 1 and 0 <= r <= 1):
        err(f"{ctx}: {kind} easingLeft/Right 应在 [0,1]: {l!r}, {r!r}")
    elif l >= r:
        err(f"{ctx}: {kind} easingLeft 应小于 easingRight: {l!r}, {r!r}")
    if e["bezier"] not in (0, 1):
        err(f"{ctx}: {kind} bezier 应为 0 或 1: {e['bezier']!r}")
    elif e["bezier"] == 1:
        bp = e["bezierPoints"]
        if not (isinstance(bp, list) and len(bp) == 4 and all(is_num(x) for x in bp)):
            err(f"{ctx}: {kind} bezierPoints 应为 4 个数值: {bp!r}")
        elif not (0 <= bp[0] <= 1 and 0 <= bp[2] <= 1):
            err(f"{ctx}: {kind} 贝塞尔控制点 x 分量应在 [0,1]: {bp!r}")
    if not isinstance(e["linkgroup"], int):
        warn(f"{ctx}: {kind} linkgroup 应为整数: {e['linkgroup']!r}")


speed_easing_fields = 0  # speedEvent 含 easing 字段的事件计数（RPE 1.7 导出行为，汇总报告）


def check_speed_event(e, ctx):
    global speed_easing_fields
    if not isinstance(e, dict):
        err(f"{ctx}: speedEvents 事件应为对象: {e!r}")
        return
    for k in ("startTime", "endTime", "start", "end", "linkgroup"):
        if k not in e:
            err(f"{ctx}: speedEvent 缺少字段 {k}: {e!r}")
            return
    check_timet(e["startTime"], f"{ctx}.speed.startTime")
    check_timet(e["endTime"], f"{ctx}.speed.endTime")
    if not is_num(e["start"]) or not is_num(e["end"]):
        err(f"{ctx}: speedEvent start/end 应为数值: {e!r}")
    # start != end 合法（流速渐变），无需警告
    for k in ("easingType", "easingLeft", "easingRight", "bezier", "bezierPoints"):
        if k in e:
            speed_easing_fields += 1


def check_extended(extended, ctx):
    if not isinstance(extended, dict):
        err(f"{ctx}.extended 应为对象: {extended!r}")
        return
    if "inclineEvents" not in extended:
        err(f"{ctx}.extended 缺少 inclineEvents")
    for kind, events in extended.items():
        if not isinstance(events, list):
            err(f"{ctx}.extended.{kind} 应为数组")
            continue
        if kind == "textEvents":
            for e in events:
                if not isinstance(e, dict) or not isinstance(e.get("start"), str) \
                        or not isinstance(e.get("end"), str):
                    err(f"{ctx}.textEvents: start/end 应为字符串: {e!r}")
                elif "startTime" in e:
                    check_timet(e["startTime"], f"{ctx}.textEvents.startTime")
                    check_timet(e.get("endTime", [0, 0, 1]), f"{ctx}.textEvents.endTime")
            continue
        if kind == "colorEvents":
            for e in events:
                if isinstance(e, dict) and isinstance(e.get("start"), str):
                    parts = e["start"].split()
                    if len(parts) != 3 or not all(p.lstrip("-").isdigit() for p in parts):
                        err(f"{ctx}.colorEvents: 值应为 'r g b' 整数三元组字符串: {e!r}")
                continue
        for e in events:
            check_normal_event(e, ctx, kind, extended=True)
        check_ascending(events, ctx, kind)


def check_notes(notes, numOfNotes, ctx):
    if not isinstance(notes, list):
        err(f"{ctx}.notes 应为数组")
        return
    if numOfNotes != len(notes):
        warn(f"{ctx}: numOfNotes({numOfNotes}) != notes 数量({len(notes)})（生成谱面时应保持一致）")
    saw_hold = False
    prev_nonhold = None
    prev_hold = None
    seen = set()
    for i, n in enumerate(notes):
        where = f"{ctx}.notes[{i}]"
        if not isinstance(n, dict):
            err(f"{where}: 音符应为对象")
            continue
        for k in ("type", "startTime", "endTime", "positionX", "above", "speed",
                  "yOffset", "isFake", "size", "visibleTime", "alpha"):
            if k not in n:
                err(f"{where}: 缺少字段 {k}: {n!r}")
                break
        else:
            t = n["type"]
            if t not in NOTE_TYPES:
                err(f"{where}: type 应为 1/2/3/4: {t!r}")
            check_timet(n["startTime"], f"{where}.startTime")
            check_timet(n["endTime"], f"{where}.endTime")
            if t != 2 and n["endTime"] != n["startTime"]:
                err(f"{where}: 非 Hold 音符 endTime 必须等于 startTime")
            if t == 2 and beat(n["endTime"]) < beat(n["startTime"]):
                err(f"{where}: Hold endTime 早于 startTime")
            if n["above"] not in (0, 1, 2):
                err(f"{where}: above 应为 0/1/2: {n['above']!r}")
            if n["isFake"] not in (0, 1):
                err(f"{where}: isFake 应为 0/1: {n['isFake']!r}")
            a = n["alpha"]
            if not is_num(a) or not (0 <= a <= 255):
                warn(f"{where}: alpha 建议 0~255: {a!r}")
            if not is_num(n["positionX"]) or abs(n["positionX"]) > 4000:
                err(f"{where}: positionX 异常: {n['positionX']!r}")
            if not is_num(n["size"]) or n["size"] <= 0:
                err(f"{where}: size 应为正数: {n['size']!r}")
            if not is_num(n["speed"]) or not is_num(n["visibleTime"]):
                err(f"{where}: speed/visibleTime 应为数值: {n!r}")
            b = beat(n["startTime"])
            if t == 2:
                saw_hold = True
                if prev_hold is not None and b < prev_hold:
                    err(f"{where}: Hold 音符未按时间升序（{prev_hold:.4f} -> {b:.4f}）")
                prev_hold = b
            else:
                if saw_hold:
                    err(f"{where}: Hold 之后出现非 Hold 音符（应非 Hold 在前、Hold 在后）")
                if prev_nonhold is not None and b < prev_nonhold:
                    err(f"{where}: 非 Hold 音符未按时间升序（{prev_nonhold:.4f} -> {b:.4f}）")
                prev_nonhold = b
            sig = (tuple(n["startTime"]), t, round(n["positionX"], 3))
            if sig in seen and not n.get("isFake"):
                warn(f"{where}: 疑似重复音符 startTime={sig[0]} type={t} x={sig[2]}")
            seen.add(sig)


def check_line_speed(line, ctx):
    """有音符的判定线：各层 speedEvents 求和后在音符时刻的值应 >= 10（0 速绑线表演除外，仅警告）。"""
    notes = line.get("notes")
    if not isinstance(notes, list) or not notes:
        return
    layer_pts = []
    for layer in line.get("eventLayers") or []:
        if not isinstance(layer, dict):
            continue
        pts = []
        for e in layer.get("speedEvents") or []:
            if not isinstance(e, dict) or not is_num(e.get("start")):
                continue
            st = e.get("startTime")
            if not (isinstance(st, list) and len(st) == 3 and all(is_num(x) for x in st)):
                continue
            pts.append((beat(st), e["start"]))
        if pts:
            pts.sort()
            layer_pts.append(pts)
    if not layer_pts:
        warn(f"{ctx}: 有音符但未定义 speedEvents，速度按 0 处理（音符线建议至少 10 速）")
        return
    speeds = []
    for n in notes:
        if not isinstance(n, dict):
            continue
        st = n.get("startTime")
        if not (isinstance(st, list) and len(st) == 3 and all(is_num(x) for x in st)):
            continue
        b = beat(st)
        total = 0.0
        for pts in layer_pts:
            v = 0.0
            for pb, pv in pts:
                if pb <= b:
                    v = pv
                else:
                    break
            total += v
        speeds.append(total)
    if speeds and min(speeds) < 10:
        bad = sum(1 for s in speeds if s < 10)
        warn(f"{ctx}: 音符线速度低于 10（最低 {min(speeds):g}，涉及 {bad}/{len(speeds)} 个音符时刻）；"
             f"仅 0 速绑线表演等特殊情况可低于 10 速，若属表演可忽略此警告")


def validate(chart):
    for k in ("BPMList", "META", "judgeLineList"):
        if k not in chart:
            err(f"顶层缺少 {k}")
            return
    if "judgeLineGroup" not in chart:
        warn("顶层缺少 judgeLineGroup（建议 [\"Default\"]）")

    bpm_list = chart["BPMList"]
    if not isinstance(bpm_list, list) or not bpm_list:
        err("BPMList 不能为空")
        return
    prev = None
    for i, seg in enumerate(bpm_list):
        ctx = f"BPMList[{i}]"
        if not isinstance(seg, dict) or not is_num(seg.get("bpm")) or seg["bpm"] <= 0:
            err(f"{ctx}: bpm 应为正数: {seg!r}")
            return
        if not check_timet(seg.get("startTime", []), f"{ctx}.startTime"):
            return
        b = beat(seg["startTime"])
        if i == 0 and seg["startTime"] != [0, 0, 1]:
            warn(f"{ctx}: 第一段 BPM 未从 [0,0,1] 开始: {seg['startTime']!r}")
        if prev is not None and b <= prev:
            err(f"{ctx}: BPM 段 startTime 未严格递增")
        prev = b

    meta = chart["META"]
    if not isinstance(meta, dict):
        err("META 应为对象")
        return
    for k in ("RPEVersion", "name", "song", "background", "id", "level",
              "composer", "charter", "offset"):
        if k not in meta:
            err(f"META 缺少 {k}")
    if "RPEVersion" in meta and not isinstance(meta["RPEVersion"], int):
        err("META.RPEVersion 应为整数")
    if "offset" in meta and not isinstance(meta["offset"], int):
        err("META.offset 应为整数毫秒")

    lines = chart["judgeLineList"]
    if not isinstance(lines, list) or not lines:
        err("judgeLineList 不能为空")
        return
    groups = chart.get("judgeLineGroup")
    group_count = len(groups) if isinstance(groups, list) else 0
    total = 0
    type_count = {1: 0, 2: 0, 3: 0, 4: 0}
    for li, line in enumerate(lines):
        ctx = f"judgeLineList[{li}]"
        if not isinstance(line, dict):
            err(f"{ctx}: 判定线应为对象")
            continue
        for k in ("Group", "Name", "Texture", "alphaControl", "bpmfactor", "eventLayers",
                  "extended", "father", "isCover", "numOfNotes", "posControl",
                  "sizeControl", "skewControl", "yControl", "zOrder"):
            if k not in line:
                err(f"{ctx} 缺少字段 {k}")
        father = line.get("father")
        if isinstance(father, int) and father != -1:
            if father == li:
                err(f"{ctx}.father 不能指向自身")
            elif not (0 <= father < len(lines)):
                err(f"{ctx}.father 越界（应为 -1 或 0~{len(lines) - 1}）: {father}")
        g = line.get("Group")
        if isinstance(g, int) and group_count and not (0 <= g < group_count):
            warn(f"{ctx}.Group={g} 超出 judgeLineGroup 范围（0~{group_count - 1}）")
        for k in ("alphaControl", "posControl", "sizeControl", "skewControl", "yControl"):
            if k in line:
                check_control_array(line[k], {"alphaControl": "alpha", "posControl": "pos",
                                              "sizeControl": "size", "skewControl": "skew",
                                              "yControl": "y"}[k], ctx)
        layers = line.get("eventLayers")
        if not isinstance(layers, list) or not (1 <= len(layers) <= 8):
            err(f"{ctx}.eventLayers 应为 1~8 个层级的数组，实际 {layers if not isinstance(layers, list) else len(layers)} 层")
            layers = []
        for lidx, layer in enumerate(layers):
            lctx = f"{ctx}.eventLayers[{lidx}]"
            if layer is None:
                # 真实谱面（RPE 1.7 导出）偶见第 5 层为 null，跳过即可
                warn(f"{lctx}: 层为 null，已跳过")
                continue
            if not isinstance(layer, dict):
                err(f"{lctx} 应为对象")
                continue
            for kind in NORMAL_EVENT_KINDS:
                events = layer.get(kind)
                if events is None:
                    continue  # 真实谱面常省略空事件数组，视为空
                if not isinstance(events, list):
                    err(f"{lctx}.{kind} 应为数组")
                    continue
                for e in events:
                    check_normal_event(e, lctx, kind)
                check_ascending(events, lctx, kind)
            sp = layer.get("speedEvents")
            if sp is None:
                continue  # 真实谱面常省略空事件数组，视为空
            elif isinstance(sp, list):
                for e in sp:
                    check_speed_event(e, lctx)
                check_ascending(sp, lctx, "speedEvents")
            else:
                err(f"{lctx}.speedEvents 应为数组")
        if "extended" in line:
            check_extended(line["extended"], ctx)
        if layers and not any(
            isinstance(layer, dict) and isinstance(layer.get("alphaEvents"), list) and layer["alphaEvents"]
            for layer in layers
        ):
            warn(f"{ctx}: 所有 eventLayers 都没有 alphaEvents 事件"
                 f"（要求每条判定线至少一条 alpha 事件，值 0~255，如 [0,0,1] 覆盖全曲）")
        if "notes" in line:
            check_notes(line["notes"], line.get("numOfNotes"), ctx)
            for n in line["notes"]:
                if isinstance(n, dict) and n.get("type") in type_count and not n.get("isFake"):
                    type_count[n["type"]] += 1
            total += len(line["notes"])
        elif line.get("numOfNotes", 0) != 0:
            warn(f"{ctx}: 无 notes 字段但 numOfNotes={line.get('numOfNotes')}")
        check_line_speed(line, ctx)
    check_chord_density(lines)
    perf_lines = sum(
        1 for line in lines
        if isinstance(line, dict) and not (line.get("notes") or [])
    )
    if perf_lines < 5:
        warn(f"纯表演判定线只有 {perf_lines} 条（用户要求：表演线无数量上限，但最少 5 条）")
    if speed_easing_fields:
        warn(f"有 {speed_easing_fields} 个 speedEvent 含 easingType/easingLeft/easingRight/bezier 字段"
             "（RPE 1.7 导出行为，播放器忽略；自建谱不要写）")
    return {"total": total, "types": type_count, "lines": len(lines), "bpm_segments": len(bpm_list)}


def check_chord_density(lines):
    """4k/5k 段落启发式：双押海允许；检测 3 押及以上高密度段落是否过长（连续 >16 拍）或过多（累计 >=40 拍）。

    押数按"同刻不同 positionX 的数量"计：同刻同 x 的叠判定（真实谱面常用的伪多押手法，单点全吃）只算 1 押。
    """
    pts = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        for n in line.get("notes") or []:
            if not isinstance(n, dict) or n.get("isFake") == 1:
                continue
            st = n.get("startTime")
            if isinstance(st, list) and len(st) == 3 and all(is_num(x) for x in st):
                pts.append((beat(st), n.get("positionX", 0.0) or 0.0))
    if len(pts) < 16:
        return
    pts.sort()
    # 同刻不同 x 的数量 = 真实押数
    by_time = {}
    for b, x in pts:
        by_time.setdefault(round(b, 6), set()).add(round(x, 3))
    times = sorted(by_time)
    W = 8.0
    heavy_windows = []
    w = 0.0
    last = times[-1]
    while w <= last:
        win = [by_time[t] for t in times if w <= t < w + W]
        if len(win) >= 8:
            heavy = sum(1 for xs in win if len(xs) >= 3)
            if heavy / len(win) >= 0.3:
                heavy_windows.append(w)
        w += W
    if not heavy_windows:
        return
    runs = []
    start = prev = heavy_windows[0]
    for x in heavy_windows[1:]:
        if x == prev + W:
            prev = x
        else:
            runs.append((start, prev + W))
            start = prev = x
    runs.append((start, prev + W))
    for s, e in runs:
        if e - s > 16:
            warn(f"拍 {s:g}~{e:g}: 3 押及以上堆叠段落过长（{e - s:g} 拍，疑似 4k/5k），"
                 f"建议拆入双押海/交互/拖动")
    total = len(heavy_windows) * W
    if total >= 40:
        warn(f"3 押及以上高密度段落全曲累计约 {total:g} 拍（{len(runs)} 段），偏多；"
             f"建议仅保留重音点缀，主体密度改用双押海/交互/拖动")


def main(argv=None):
    ap = argparse.ArgumentParser(description="校验 RPE JSON 谱面")
    ap.add_argument("chart", help="RPE JSON 文件路径")
    ap.add_argument("--quiet-warnings", action="store_true", help="不打印 WARNING")
    args = ap.parse_args(argv)

    try:
        with open(args.chart, encoding="utf-8") as f:
            chart = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: 无法读取谱面: {e}", file=sys.stderr)
        return 1

    stats = validate(chart)
    for m in errors:
        print("ERROR:", m)
    if not args.quiet_warnings:
        for m in warnings:
            print("WARNING:", m)
    if stats:
        print(f"统计: 判定线 {stats['lines']} 条, 音符 {stats['total']} 个 "
              f"(Tap {stats['types'][1]}, Hold {stats['types'][2]}, "
              f"Flick {stats['types'][3]}, Drag {stats['types'][4]}), "
              f"BPM 段 {stats['bpm_segments']}")
    if errors:
        print(f"校验失败: {len(errors)} 个 ERROR, {len(warnings)} 个 WARNING")
        return 1
    print(f"校验通过: 0 ERROR, {len(warnings)} 个 WARNING")
    return 0


if __name__ == "__main__":
    sys.exit(main())
