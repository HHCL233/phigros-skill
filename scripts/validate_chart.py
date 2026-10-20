#!/usr/bin/env python3
"""校验 RPE JSON 谱面。

用法：python3 validate_chart.py <chart.json> [--audio <音频文件>] [--quiet-warnings]
提供 --audio 时校验：① 音符不晚于音频总时长（越界 ERROR）；② 虚空采音（音符时刻附近无频谱起音，汇总 WARNING）。
ERROR 全部修复才算可用；WARNING 酌情处理。退出码 0=无 ERROR，1=有 ERROR。
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import wave

try:
    import numpy as np  # 仅「虚空采音」起音对齐检查需要，缺失时跳过该检查
except ImportError:
    np = None

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


def bpm_segments(chart):
    """按起始拍升序返回 [(起始拍, bpm), ...]（BPMList 中字段非法的段忽略）。"""
    segs = []
    for seg in chart.get("BPMList") or []:
        if not isinstance(seg, dict):
            continue
        st = seg.get("startTime")
        if isinstance(st, list) and len(st) == 3 and all(is_num(x) for x in st) and is_num(seg.get("bpm")):
            segs.append((beat(st), float(seg["bpm"])))
    segs.sort()
    return segs


def beat_to_sec(b, segs, offset_ms):
    """按 BPM 段把拍换算为秒（beat 0 对应音乐第 offset_ms/1000 秒，与 time_conv.py 同口径）。"""
    sec = (offset_ms or 0) / 1000.0
    if not segs:
        return sec + b * 0.5
    cur_b, cur_bpm = segs[0]
    for pb, bpm in segs[1:]:
        if b <= pb:
            break
        sec += (pb - cur_b) * 60.0 / cur_bpm
        cur_b, cur_bpm = pb, bpm
    return sec + (b - cur_b) * 60.0 / cur_bpm


def audio_duration_sec(path):
    """音频总时长（秒）。WAV 走标准库，其余优先 ffprobe、退回 ffmpeg -i；都不可用返回 None。"""
    if os.path.splitext(path)[1].lower() == ".wav":
        try:
            with wave.open(path, "rb") as w:
                return w.getnframes() / w.getframerate()
        except Exception:
            return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, timeout=60)
        if out.returncode == 0:
            v = float(out.stdout.decode(errors="ignore").strip())
            if v > 0:
                return v
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.run(["ffmpeg", "-i", path, "-f", "null", "-"],
                             capture_output=True, timeout=120)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
                      out.stderr.decode(errors="ignore"))
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def notes_beyond_audio(chart, duration):
    """音符（含 Hold 结束时刻）是否晚于音频总时长；返回消息列表。

    用户要求：note 不得写在比音乐总时间还晚的位置。秒数依赖 META.offset 与 BPMList，
    故需调用方提供音频时长；无法取得时长时不检查。
    """
    segs = bpm_segments(chart)
    offset = (chart.get("META") or {}).get("offset") or 0
    latest = None  # (秒, 最晚音符 TimeT, 定位串)
    for li, line in enumerate(chart.get("judgeLineList") or []):
        if not isinstance(line, dict):
            continue
        for i, n in enumerate(line.get("notes") or []):
            if not isinstance(n, dict):
                continue
            for key in ("startTime", "endTime"):
                t = n.get(key)
                if isinstance(t, list) and len(t) == 3 and all(is_num(x) for x in t):
                    s = beat_to_sec(beat(t), segs, offset)
                    if latest is None or s > latest[0]:
                        latest = (s, t, f"{key} of judgeLineList[{li}].notes[{i}]")
    if latest is None or latest[0] <= duration + 0.05:
        return []
    return [f"音符超出音频总时长：最晚的 {latest[2]} = {latest[1]!r}（≈{latest[0]:.2f}s）"
            f"晚于音频结束 {duration:.2f}s"
            f"——用户要求：note 不得写在比音乐总时间还晚的位置（含 Hold 结束时刻）"]


def check_audio_bounds(chart, duration):
    for m in notes_beyond_audio(chart, duration):
        err(m)


# ---------------------------------------------------------------------------
# 虚空采音检测（需 --audio）：每个可交互音符时刻附近应有真实频谱起音
# ---------------------------------------------------------------------------

VOID_LOCAL_RATIO = 0.25   # 音符窗口内起音强度 < 局部参考的该比例 => 疑似虚空采音
VOID_WARN_RATIO = 0.06    # 疑似占比 >= 该比例 => WARNING
VOID_LOCAL_WIN = 2.5      # 局部参考窗口半宽（秒）
VOID_LOCAL_PCT = 95.0     # 局部参考取该分位（近似该处起音峰值水平）


def resample_linear(x, sr0, sr1):
    n = int(round(len(x) * sr1 / float(sr0)))
    if n <= 1:
        return x
    return np.interp(np.arange(n) / float(sr1), np.arange(len(x)) / float(sr0), x)


def decode_mono(path, sr=22050):
    """解码为单声道 [-1,1] 浮点数组；WAV 走标准库，其余用 ffmpeg；失败返回 None。"""
    if np is None:
        return None
    if os.path.splitext(path)[1].lower() == ".wav":
        try:
            with wave.open(path, "rb") as w:
                ch, sw, fr = w.getnchannels(), w.getsampwidth(), w.getframerate()
                raw = w.readframes(w.getnframes())
            if sw not in (1, 2, 4):
                return None
            x = np.frombuffer(raw, dtype={1: np.int8, 2: "<i2", 4: "<i4"}[sw]).astype(np.float64)
            if ch > 1:
                x = x.reshape(-1, ch).mean(axis=1)
            x /= float(2 ** (8 * sw - 1))
            return resample_linear(x, fr, sr) if fr != sr else x
        except Exception:
            return None
    try:
        out = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le",
                              "-ac", "1", "-ar", str(sr), "-"],
                             capture_output=True, timeout=600)
        if out.returncode != 0 or not out.stdout:
            return None
        return np.frombuffer(out.stdout, dtype="<f4").astype(np.float64)
    except (OSError, subprocess.SubprocessError):
        return None


def onset_envelope(x, sr, win=1024, hop=256):
    """起音包络：帧对数幅度谱的正向差分和（spectral flux）；返回 (flux, 帧时刻秒)。"""
    if len(x) < win * 2:
        return None, None
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop]
    mag = np.abs(np.fft.rfft(frames * np.hanning(win), axis=1))
    diff = np.diff(np.log1p(10.0 * mag), axis=0)
    flux = np.maximum(0.0, diff).sum(axis=1)
    return flux, (np.arange(len(flux)) + 1) * hop / float(sr)


def bpm_at(segs, b):
    if not segs:
        return 120.0
    cur = segs[0][1]
    for pb, bpm in segs:
        if b < pb:
            break
        cur = bpm
    return cur


def check_void_onset(chart, audio):
    """虚空采音检测（用户要求）：音符时刻 ±min(半拍, 60ms) 内须有频谱起音。

    逐音取窗口内起音强度峰值，与 ±2.5s 局部起音的高分位（95%）比较；明显偏低者记为疑似虚空采音。
    仅在占比达到阈值时输出一条汇总 WARNING（避免逐音刷屏），疑似音符输出前几个作样例。
    """
    x = decode_mono(audio)
    if np is None:
        warn("未安装 numpy，跳过虚空采音检查（pip install numpy 后启用）")
        return
    if x is None:
        warn(f"无法解码音频（{audio}），跳过虚空采音检查（需 ffmpeg；WAV 可直读）")
        return
    flux, times = onset_envelope(x, 22050)
    if flux is None:
        warn(f"音频过短（{audio}），跳过虚空采音检查")
        return
    segs = bpm_segments(chart)
    offset = (chart.get("META") or {}).get("offset") or 0
    suspects, total = [], 0
    for li, line in enumerate(chart.get("judgeLineList") or []):
        if not isinstance(line, dict):
            continue
        for i, n in enumerate(line.get("notes") or []):
            if not isinstance(n, dict) or n.get("isFake") or n.get("type") not in (1, 2, 3, 4):
                continue
            st = n.get("startTime")
            if not (isinstance(st, list) and len(st) == 3 and all(is_num(x) for x in st)):
                continue
            total += 1
            b = beat(st)
            t = beat_to_sec(b, segs, offset)
            tol = max(0.025, min(0.5 * 60.0 / bpm_at(segs, b), 0.06))
            i0, i1 = np.searchsorted(times, [t - tol, t + tol])
            i0, i1 = int(max(0, i0 - 1)), int(min(len(flux), i1 + 1))
            if i1 <= i0:
                continue
            strength = float(flux[i0:i1].max())
            w0, w1 = np.searchsorted(times, [t - VOID_LOCAL_WIN, t + VOID_LOCAL_WIN])
            local = flux[int(w0):int(w1)] if w1 > w0 else flux
            ref = float(np.percentile(local, VOID_LOCAL_PCT)) if len(local) else 0.0
            if ref > 0 and strength < VOID_LOCAL_RATIO * ref:
                suspects.append((round(t, 2), st, f"judgeLineList[{li}].notes[{i}]", strength, ref))
    if not total:
        return
    ratio = len(suspects) / float(total)
    if len(suspects) >= 3 and ratio >= VOID_WARN_RATIO:
        sample = "; ".join(f"{s[2]} = {s[1]!r}（≈{s[0]}s，起音强度仅为局部参考的 "
                          f"{100.0 * s[3] / s[4]:.0f}%）" for s in suspects[:3])
        warn(f"疑似虚空采音：{len(suspects)}/{total} 个音符（{100 * ratio:.1f}%）时刻附近没有频谱起音"
             f"——用户要求：每个可交互音符都要对应真实音乐事件（kick/snare/hihat 起音、旋律音头、人声字头），"
             f"不得为凑密度/凑配置在没有起音处打音符。样例：{sample}…"
             f"（若为长音拖尾/氛围段等合法情形可忽略，或用 --quiet-warnings 静音）")



def check_ascending(events, ctx, kind):
    prev = None
    prev_end = None  # 上一事件的结束拍，用于同 kind 区间重叠检查
    overlap_hit = False
    for e in events:
        t = e.get("startTime")
        if not isinstance(t, list) or len(t) != 3:
            continue
        b = beat(t)
        et = e.get("endTime")
        b_end = beat(et) if isinstance(et, list) and len(et) == 3 and all(is_num(x) for x in et) else None
        if prev is not None:
            if b < prev:
                err(f"{ctx}: {kind} 事件未按时间升序（{prev:.4f} -> {b:.4f}）")
            elif b == prev:
                warn(f"{ctx}: {kind} 存在相同 startTime 的事件 @{t!r}")
            # 同 kind 事件区间重叠：同层内两个同类型事件在相同时间内重复（真实谱 12 张实测 0 例）
            if b_end is not None and prev_end is not None and b < prev_end - 1e-9 and not overlap_hit:
                warn(f"{ctx}: {kind} 事件时间区间重叠（上一事件结束 {prev_end:.4f} 拍 > 当前开始 {b:.4f} 拍）"
                     f"（用户要求：同一判定线同一层内相同时间不要有两个同类型重复事件）")
                overlap_hit = True  # 每层每 kind 只报一次，避免刷屏
        prev = b
        prev_end = b_end


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
            # 真实谱面两种写法：字符串 "r g b" 或 RGB 数组 [r,g,b]（RPE 1.7 导出）
            for e in events:
                if not isinstance(e, dict):
                    continue
                s = e.get("start")
                if isinstance(s, str):
                    parts = s.split()
                    if len(parts) != 3 or not all(p.lstrip("-").isdigit() for p in parts):
                        err(f"{ctx}.colorEvents: 值应为 'r g b' 整数三元组字符串: {e!r}")
                elif isinstance(s, list):
                    if len(s) != 3 or not all(isinstance(v, (int, float)) and 0 <= v <= 255 for v in s):
                        warn(f"{ctx}.colorEvents: RGB 数组应为 3 个 0~255 分量: {e!r}")
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
    hold_ends = []  # (beat, x)：非假 Hold 的结束时刻与 x，用于"长条末尾同 x 不放 Tap"检查
    hold_spans = []  # (start, end, x)：Hold 持续区间，用于"Hold 期间近距 Tap"检查
    drag_starts = []  # (beat, x)：非假 Drag 起始，用于"Drag 后紧跟 x 相近 Tap"检查
    for n in notes:
        if isinstance(n, dict) and n.get("type") == 2 and not n.get("isFake") \
                and isinstance(n.get("endTime"), list) and is_num(n.get("positionX")):
            hold_ends.append((beat(n["endTime"]), n["positionX"]))
            hold_spans.append((beat(n["startTime"]), beat(n["endTime"]), n["positionX"]))
        if isinstance(n, dict) and n.get("type") == 4 and not n.get("isFake") \
                and is_num(n.get("positionX")):
            drag_starts.append((beat(n["startTime"]), n["positionX"]))
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
            if b < 1 - 1e-6 and not n.get("isFake"):
                warn(f"{where}: 可判音符早于 beat [1,0,1]"
                     f"（用户要求：开头不出 note，最早 1:0/1，且开头建议整段纯表演）")
            if t == 1 and not n.get("isFake") and any(
                abs(b - he) < 1e-6 and abs(n["positionX"] - hx) < 1e-6 for he, hx in hold_ends
            ):
                warn(f"{where}: Tap 位于长条（Hold）末尾的同 x 位置"
                     f"（用户要求：Hold 结束的同 x 不放 Tap；Drag/Flick 接续长条是允许的）")
            if t == 1 and not n.get("isFake"):
                # Hold 期间近距 Tap：同线 Hold 持续期间（含首时刻），|Δx| ≤ 50 的 Tap 不要（用户要求间距必须 >50）
                if any(hs - 1e-6 <= b < he - 1e-6 and abs(n["positionX"] - hx) <= 50
                       for hs, he, hx in hold_spans):
                    warn(f"{where}: Tap 与进行中的长条（Hold）距离过近（|Δx| ≤ 50）"
                         f"（用户要求：相同时间内 Tap 与 Hold 间距必须 > 50）")
                # Drag 后紧跟 x 相近的 Tap：gap ≤ 1/4 拍 且 |Δx| < 50
                if any(0 < b - dg <= 0.25 + 1e-6 and abs(n["positionX"] - dx) < 50
                       for dg, dx in drag_starts):
                    warn(f"{where}: Tap 紧跟在 Drag 之后且 x 相近（间隔 ≤ 1/4 拍、|Δx| < 50）"
                         f"（用户要求：不要出现一个 Drag 后紧跟一个 x 相近的 Tap）")
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

    # 同刻间距检查：同判定线同一时刻，Tap-Tap 或 Tap-Hold 的 x 间距必须 > 50（用户明确要求）
    taps_by_beat = {}  # beat -> [(x, where)]
    holds_by_beat = {}  # beat -> [x]
    for i, n in enumerate(notes):
        if not isinstance(n, dict) or n.get("isFake"):
            continue
        if not (isinstance(n.get("startTime"), list) and len(n["startTime"]) == 3):
            continue
        b = beat(n["startTime"])
        if n.get("type") == 1 and is_num(n.get("positionX")):
            taps_by_beat.setdefault(b, []).append((n["positionX"], i))
        elif n.get("type") == 2 and is_num(n.get("positionX")):
            holds_by_beat.setdefault(b, []).append(n["positionX"])
    for b, items in taps_by_beat.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if abs(items[i][0] - items[j][0]) <= 50:
                    warn(f"{ctx}.notes[{items[j][1]}]: 同刻两个 Tap 间距 ≤ 50"
                         f"（x={items[i][0]} 与 x={items[j][0]} @beat {b:.4f}；用户要求：相同时间内同判定线两个 Tap 间距必须 > 50）")
        for tx, idx in items:
            for hx in holds_by_beat.get(b, []):
                if abs(tx - hx) <= 50:
                    warn(f"{ctx}.notes[{idx}]: 同刻 Tap 与 Hold 间距 ≤ 50"
                         f"（Tap x={tx} 与 Hold x={hx} @beat {b:.4f}；用户要求：相同时间内同判定线 Tap 与 Hold 间距必须 > 50）")

    # 双押海 / 纵连过长检查：均为高密度手段，用户要求少用（仅非假 Tap）
    if taps_by_beat:
        bs = sorted(taps_by_beat)
        cur = 0
        max_dd = 0
        for i, b in enumerate(bs):
            if len(taps_by_beat[b]) >= 2 and i > 0 and b - bs[i - 1] <= 0.26 + 1e-9:
                cur += 1
            elif len(taps_by_beat[b]) >= 2:
                cur = 1
            else:
                cur = 0
            max_dd = max(max_dd, cur)
        if max_dd > 4:
            warn(f"{ctx}: 双押海过长（最长连续 {max_dd} 组双押 > 4）"
                 f"——用户要求：少用双押海，单段 ≤4 组即收，穿插单点/交互/Drag")
        by_x = {}
        for b, items in taps_by_beat.items():
            for x, _ in items:
                by_x.setdefault(x, []).append(b)
        max_jack = 0
        for x, bl in by_x.items():
            bl.sort()
            cur = 1 if bl else 0
            for i in range(1, len(bl)):
                cur = cur + 1 if bl[i] - bl[i - 1] <= 0.26 + 1e-9 else 1
                max_jack = max(max_jack, cur)
        if max_jack > 6:
            warn(f"{ctx}: 同 x 纵连过长（最长 {max_jack} 连 > 6）"
                 f"——用户要求：少用纵连，单段 ≤6 连即收")


def check_line_speed(line, ctx):
    """有音符的判定线：各层 speedEvents 求和后在音符时刻的值应 >= 10（仅警告）。
    可判音符位于 0 速线（note 绑线）单独警告——用户要求不使用任何 note 绑线。"""
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
    zero_bind = 0
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
        if total <= 1e-6 and not n.get("isFake"):
            zero_bind += 1
    if zero_bind:
        warn(f"{ctx}: 存在可判音符位于 0 速线（note 绑线，{zero_bind} 个音符时刻）"
             f"——用户要求：不使用任何 note 绑线，0 速线不放音符；演出改用手法库线运动/alpha/文字（假音符亦禁用）")
    if speeds and min(speeds) < 10:
        bad = sum(1 for s in speeds if s < 10)
        warn(f"{ctx}: 音符线速度低于 10（最低 {min(speeds):g}，涉及 {bad}/{len(speeds)} 个音符时刻）")


def validate(chart, audio_duration=None, audio_path=None):
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
        # 表演线 alpha 上限：无可判音符的线（纯表演/道具线）alpha 不得大于 200
        has_judge = any(
            isinstance(n, dict) and not n.get("isFake") for n in (line.get("notes") or [])
        )
        if layers and not has_judge:
            for layer in layers:
                if not isinstance(layer, dict):
                    continue
                mx = None
                for e in (layer.get("alphaEvents") or []):
                    if isinstance(e, dict):
                        for key in ("start", "end"):
                            v = e.get(key)
                            if is_num(v):
                                mx = v if mx is None else max(mx, v)
                if mx is not None and mx > 200:
                    warn(f"{ctx}: 表演判定线 alpha 最大 {mx} 大于 200"
                         f"（要求表演线 alpha 不大于 200，保持背景感；真实谱面常见 255 属风格差异）")
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
    check_x_grid(lines)
    if audio_duration:
        check_audio_bounds(chart, audio_duration)
    if audio_path:
        check_void_onset(chart, audio_path)
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
    另检查 4 押及以上（同刻不同 x >=4）：用户要求自建谱一律禁止，真实谱面存在，故仅 WARNING。
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
    over = [(t, len(by_time[t])) for t in times if len(by_time[t]) >= 4]
    if over:
        mx = max(c for _, c in over)
        sample = "、".join(f"拍 {t:g}（{c} 押）" for t, c in over[:3])
        more = f" 等" if len(over) > 3 else ""
        warn(f"出现 {len(over)} 处 4 押及以上（同刻不同 x >=4，最大 {mx} 押）：{sample}{more}"
             f"（用户要求：自建谱一律禁止 4 押及以上，重音改用双押/3 押/同位叠判定/Drag；"
             f"真实谱面存在 4~6 押属风格差异，故仅 WARNING）")
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


GRID_STEPS = (5.0, 6.25, 7.5, 11.25, 12.5, 15.0, 18.75, 22.5, 25.0, 30.0, 33.75,
              37.5, 45.0, 50.0, 52.5, 56.25, 67.5, 75.0, 90.0, 112.5, 135.0, 150.0,
              168.75, 225.0, 337.5)  # 常用网格间隔（75 系 / 67.5 系 / 52.5 系 / 等分点）
GRID_TOL = 1.0       # x 落点容差（屏幕单位）
GRID_MIN_STEP = 5.0  # 小于该间隔视为"没有网格"
GRID_MIN_COVER = 0.85  # 覆盖率下限


def check_x_grid(lines):
    """排键整齐：全曲音符的 x 应落在同一套网格上。

    做法：聚类去重所有（非假）音符的 |positionX|，以「相邻簇间距 ∪ 常用网格间隔」为候选步长，
    取覆盖率（落在步长整数倍容差内的音符占比）最高者；覆盖率不足即视为排键不整齐（仅 WARNING）。
    网格族/自定义对称网格本就不止一种，真实谱面亦有连续坐标风格，故不做硬校验。
    """
    xs = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        for n in line.get("notes") or []:
            if not isinstance(n, dict) or n.get("isFake") == 1:
                continue
            x = n.get("positionX")
            if is_num(x):
                xs.append(abs(round(float(x), 3)))
    if len(xs) < 16:
        return
    clusters = []
    for x in sorted(set(xs)):
        if not clusters or x - clusters[-1] > GRID_TOL:
            clusters.append(x)
    cands = {s for s in GRID_STEPS if s >= GRID_MIN_STEP}
    cands.update(round(b - a, 4) for a, b in zip(clusters, clusters[1:])
                 if b - a >= GRID_MIN_STEP)
    best_cov, best_step = 0.0, None
    for s in sorted(cands):
        cov = sum(1 for x in xs if abs(x - round(x / s) * s) <= GRID_TOL) / len(xs)
        if cov > best_cov or (cov == best_cov and s > (best_step or 0)):
            best_cov, best_step = cov, s
    if best_cov < GRID_MIN_COVER:
        warn(f"排键不整齐：{len(xs)} 个音符的 x 未落在统一网格上"
             f"（最接近的网格间隔 {best_step:g} 仅覆盖 {best_cov:.0%}）"
             f"——要求全曲统一一套 x 网格（75 系 / 67.5 系 / 自定义对称网格），段内步进规整、左右对称；"
             f"不规则乱序坐标少用（配置库 B2 仅在用户要求该风格时用）")


def main(argv=None):
    ap = argparse.ArgumentParser(description="校验 RPE JSON 谱面")
    ap.add_argument("chart", help="RPE JSON 文件路径")
    ap.add_argument("--audio", default=None,
                    help="音频文件路径（提供后校验音符不晚于音频总时长，并做虚空采音起音对齐检查）")
    ap.add_argument("--quiet-warnings", action="store_true", help="不打印 WARNING")
    args = ap.parse_args(argv)

    try:
        with open(args.chart, encoding="utf-8") as f:
            chart = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: 无法读取谱面: {e}", file=sys.stderr)
        return 1

    duration = None
    if args.audio:
        duration = audio_duration_sec(args.audio)
        if duration is None:
            warnings.append(f"无法读取音频时长（{args.audio}），跳过音符越界检查"
                            f"（需 ffprobe/ffmpeg；WAV 可直读）")
    stats = validate(chart, duration, args.audio)
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
