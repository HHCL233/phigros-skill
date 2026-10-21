# RPE JSON 谱面格式规范

依据 info.md 与真实 RPE 谱面归纳。

## 时间系统（最重要，勿算错）

所有时间均为 `TimeT = [beat, num, den]` 三元组（带分数），表示 `beat + num/den` **拍**。
**第一位是"拍"，不是"小节"**——按小节理解会导致 4 倍时间错误。
1 拍 = 一个四分音符 = `60/BPM` 秒。

换算（单 BPM 段，offset 为 META 中的毫秒偏移，正值 = 谱面相对音乐延后）：

- 拍 → 秒：`sec = (beat + num/den) × 60/BPM + offset/1000`
- 秒 → 拍：`beat_total = (sec − offset/1000) × BPM/60`，再拆成 `[整数拍, 分子, 分母]`
- 即 beat 0 判定于音乐第 `offset/1000` 秒；bpm_detect 输出的 `suggested_offset_ms` 可直接用作 META.offset

多 BPM 段（BPMList）换算须分段累计：

```
sec(b) = S_i + (b − B_i) × 60/bpm_i    （B_i ≤ b < B_{i+1}，B_i 为第 i 段起始拍）
S_0 = offset/1000；S_{i+1} = S_i + (B_{i+1} − B_i) × 60/bpm_i
```

示例：offset=0，段1 [0,0,1] 起 128 BPM，段2 [96,0,1] 起 150 BPM：
beat 96 → 96×60/128 = 45.000s；beat 100 → 45.0 + 4×60/150 = 46.600s。
**换算一律使用 scripts/time_conv.py，不要手算。**

常见音符时值与 TimeT 对照（4/4 拍）：

| 时值 | 拍数 | TimeT 写法 |
| --- | --- | --- |
| 四分音符 | 1 | `[b, 0, 1]` |
| 八分音符 | 1/2 | `[b, 1, 2]` |
| 十六分音符 | 1/4 | `[b, 1, 4]` |
| 三十二分音符 | 1/8 | `[b, 1, 8]` |
| 三连八分音符 | 1/3 | `[b, 1, 3]` |
| 三连十六分音符 | 1/6 | `[b, 1, 6]` |
| 附点八分音符 | 3/4 | `[b, 3, 4]` |

## 顶层结构

```json
{
  "BPMList": [ { "bpm": 170.0, "startTime": [0, 0, 1] } ],
  "META": { ... },
  "judgeLineGroup": ["Default"],
  "judgeLineList": [ ... ],
  "multiScale": 1.0,
  "multiLineString": ""
}
```

- `BPMList`：BPM 段列表。每段从其 `startTime`（拍）起生效，直到下一段的 `startTime`。按时间升序排列，第一段必须从 `[0,0,1]` 开始。`bpm` 为浮点数（可为非整数，如 191.01、92.51，取测得的精确值）。
- `judgeLineGroup`：判定线组名列表，线通过 `Group` 下标引用。单线谱用 `["Default"]` 即可。
- 可选编辑器元数据（可省略，校验器会忽略）：`chartTime`（浮点）、`multiLineString`（如 `"0:50"`）、`multiScale`（浮点，默认 1.0，编辑器缩放，可非 1 如 0.233，不影响游玩）、`xybind`（RPE 1.7+，布尔，见过 `false`）。

## META

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `RPEVersion` | 是 | 整数。真实谱面见 130 / 140 / 150 / 170；150 起可有 `duration`/`illustration`/`anchor`/`isGif`，170 导出还会带额外字段 |
| `name` | 是 | 谱面名（曲名） |
| `song` | 是 | 音频文件相对路径（`.mp3` / `.ogg` 均见） |
| `background` | 是 | 背景图相对路径（`.jpg` / `.png` 均见，可与封面同名） |
| `id` | 是 | 谱面 ID 字符串 |
| `level` | 是 | 难度字符串，如 `"IN Lv.15"`、`"SP Lv.?"`、`"AT Lv.13"` |
| `composer` | 是 | 曲师 |
| `charter` | 是 | 谱师 |
| `offset` | 是 | 谱面偏移，整数毫秒，默认 0。**可为负**（正值 = 谱面相对音乐延后，负值 = 提前） |
| `duration` | 否 | 音频时长（秒，浮点），RPE 1.5.0+（`illustration` 可为空字符串） |
| `illustration` | 否 | 画师，RPE 1.5.0+ |

## 判定线 JudgeLine

```json
{
  "Group": 0,
  "Name": "Untitled",
  "Texture": "line.png",
  "father": -1,
  "isCover": 1,
  "notes": [ ... ],
  "numOfNotes": 424,
  "bpmfactor": 1.0,
  "zOrder": 0,
  "eventLayers": [ { ... } ],
  "extended": { "inclineEvents": [ ... ] },
  "alphaControl":  [ { "easing": 1, "x": 0.0, "alpha": 1.0 }, { "easing": 1, "x": 9999999.0, "alpha": 1.0 } ],
  "posControl":    [ { "easing": 1, "x": 0.0, "pos": 1.0 },   { "easing": 1, "x": 9999999.0, "pos": 1.0 } ],
  "sizeControl":   [ { "easing": 1, "x": 0.0, "size": 1.0 },  { "easing": 1, "x": 9999999.0, "size": 1.0 } ],
  "skewControl":   [ { "easing": 1, "x": 0.0, "skew": 0.0 },  { "easing": 1, "x": 9999999.0, "skew": 0.0 } ],
  "yControl":      [ { "easing": 1, "x": 0.0, "y": 1.0 },     { "easing": 1, "x": 9999999.0, "y": 1.0 } ]
}
```

- `Group`：判定线组下标；`Name`：线名（建议按功能命名，如 `main` / `drag1`~`drag8` / `flick1`~`flick6`）；`Texture`：贴图路径，默认 `"line.png"`。
- `father`：父线线号（0 起下标），子线挂靠父线坐标系，跟随父线移动/旋转做组合演出。无父线为 -1，简单谱面保持 -1。
- `isCover`：1 表示遮罩开启。`zOrder`：重叠顺序，一般 0。
- `bpmfactor`：BPM 因数，保持 1.0。
- `numOfNotes`：必须等于 `notes` 长度。**没有音符的线可整体省略 `notes` 字段**。真实谱面大量出现 numOfNotes 与实际数量不一致（编辑器行为，校验器仅警告），自建谱面必须一致。
- 五个 control 数组：控制"音符控制"类参数（进阶功能），照抄默认两点写法即可（`x` 取 `0.0` 与 `9999999.0` 覆盖全程，值保持 1.0/0.0 默认）。
- RPE 1.5.0+ 可选：`"anchor": [0.5, 0.5]`、`"isGif": false`（Texture 为 gif 时 true）。

## 音符 Note

```json
{
  "type": 1,
  "startTime": [8, 0, 1],
  "endTime": [8, 0, 1],
  "positionX": -337.5,
  "above": 1,
  "speed": 1.0,
  "yOffset": 0.0,
  "isFake": 0,
  "size": 1.0,
  "visibleTime": 999999.0,
  "alpha": 255
}
```

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `type` | 必填 | 1=Tap（蓝），2=Hold（长条），3=Flick（红），4=Drag（黄） |
| `startTime` | 必填 | 判定时刻 |
| `endTime` | 必填 | 仅对 Hold 有意义；其余类型必须等于 startTime |
| `positionX` | 必填 | 在判定线上的落点 x（像素，随线旋转/缩放变换） |
| `above` | 1 | 1=从线上方落下，0/2=下方 |
| `speed` | 1.0 | 相对该线流速的倍率，可为负（配合遮罩做隐藏） |
| `yOffset` | 0.0 | 落点 y 偏移（受 above 与 speed 影响） |
| `isFake` | 0 | 1=假音符，不判定（官谱用于演出；**本 skill 禁用，恒填 0**） |
| `size` | 1.0 | 宽度倍率。标准宽度约为可视区 1/4（≈337.5），四键并排铺满 |
| `visibleTime` | 999999.0 | 判定前多少秒开始显现 |
| `alpha` | 255 | 不透明度 0~255 |

补充的真实谱面行为（合法，自建谱可参考）：

- `above` 实际见 0/1/2 三种取值（1=线上方，0/2=线下方）。
- 假音符（isFake=1）在官谱里常与 `speed: 0.0` 组合贴在 0 速表演线上当道具——**本 skill 禁用假音符，不采用此写法**。
- Hold 时值可以极短（最短 1/16 拍，作视觉"拖尾 Tap"），也可长达几十拍做铺底。
- `speed`（音符相对线流速倍率）可为 0 或负，配合遮罩做隐藏。
- **孪生副线**：两条音符线放完全相同的音符（同刻同型同 x），主线照常流动、副线额外呈现同一份下落；同刻同位叠判定（同 x 多个判定）也是合法重音手法，单点全吃。

RPE 1.5.0+ 可选 `"hitSound": "文件名"`（打击音效）。

排序规则：先非 Hold（type 1/3/4）后 Hold（type 2），组内按 startTime 升序。

## 事件 Event

普通事件（moveX/moveY/rotate/alpha/speed 五类）示例：

```json
{
  "startTime": [0, 0, 1],
  "endTime": [1, 0, 1],
  "start": 0.0,
  "end": 0.0,
  "easingType": 1,
  "easingLeft": 0.0,
  "easingRight": 1.0,
  "bezier": 0,
  "bezierPoints": [0.0, 0.0, 0.0, 0.0],
  "linkgroup": 0
}
```

- `speedEvents` **没有** `easingType/easingLeft/easingRight/bezier/bezierPoints` 字段，只有
  `startTime/endTime/start/end/linkgroup`。start 与 end 可以不同（表示流速渐变）；RPE 速度值：1 单位 ≈ 每秒下落 120px。
  **放可判音符的判定线速度至少 10**（10~12 常用；不使用 note 绑线——可判音符与假音符都不放 0 速线，0 速线不放置任何音符）；极端速度（慢段 <10、冲线段 100+、负速）属风格化手法，自建谱不模仿。
  （RPE 1.7 导出会在 speedEvents 里带上 easing 字段，属编辑器行为；自建谱**不要**写。）
- 其余四类 start/end 含义：moveX/moveY 为像素（x ∈ [-675,675]，y ∈ [-450,450]）；rotate 为角度（顺时针为正，0 = 水平）；alpha 为 0~255（负值时整线连同音符不渲染）。
- `easingType` 0~29 见 [easing.md](easing.md)（RPE 1.5+ 扩展至 49，自建谱面优先 0~29）；`easingLeft/easingRight` 截取缓动曲线区间（0≤left<right≤1）。
  **extended 事件例外**：inclineEvents 的默认占位事件 easingType 可能随线号递增远超 49（RPE 1.7 导出行为，start=end=0 时值恒定不生效），校验器仅警告；自建谱仍写 0。
- bezier：`bezier: 1` 时 `bezierPoints = [c1x, c1y, c2x, c2y]` 为三次贝塞尔两控制点（c1x、c2x ∈ [0,1]），此时忽略 easingType。
- 层内同类事件按 startTime 升序；谱面开头应有覆盖到 0 时刻的事件（通常从 `[0,0,1]` 开始）。
- `linkgroup`：多事件编辑链接组，默认 0。
- 缓动选用参考（详见 [easing.md](easing.md)）：音符线 moveX 用 1（linear，匀速下落观感）；**其余判定线移动（moveX/moveY/rotate）尽量不用 1 号**，表演线/主线过渡用 2/3（sine）、4/5（quad）、8/9（cubic）、18/19（circ）、20/21（back），少数 24（elastic Out）做强调；只有恒定值事件仍用 1 号。事件总量上 moveX/alpha/rotate 远多于 moveY。

### 事件层级 eventLayers

1~8 个元素的数组（常用 1~4 层，RPE 1.7 导出可见 5 层甚至含 `null` 元素），每层结构：

```json
{ "moveXEvents": [], "moveYEvents": [], "rotateEvents": [], "alphaEvents": [], "speedEvents": [] }
```

最终普通事件值 = 各层对应事件之和。只需要一层时写一层。层内某类事件为空时建议保留空数组（校验器兼容整类省略）。

### 扩展事件 extended

```json
"extended": {
  "inclineEvents": [ { ...标准事件字段..., "easingType": 0, "start": 0.0, "end": 0.0 } ]
}
```

- `inclineEvents`：线的倾斜（透视），默认一条 0 值事件即可，**必须存在**。
- 可选：`scaleXEvents` / `scaleYEvents`（缩放，1.0 为原大）、`colorEvents`（线的变色渐变，`start`/`end` 为颜色值——字符串 `"255 255 255"` 或 RGB 数组 `[255,255,255]` 两种写法都存在，事件含完整缓动字段；自建谱建议恒色一条即可）、`textEvents`（把线变文字，`start`/`end` 为文本；支持 `\n`、`%P%` 数字缓动、子串加字/减字；**慎用**）。
- `textEvents` 格式参考（仅在用户明确要求时少量点缀）：恒定文字 = start==end（如 `"Beats:"`）；动态数字 =
  `start: "0%P%"` → `end: "496%P%"`（`%P%` 位置随缓动从 0 变到 496，可做拍数计步器）；
  多段事件在不同时间段切换不同文本。
- 着色器事件存于谱面同目录 `extra.json`，本 skill 暂不生成。

## 打包（.pez 与 info.txt）

pez = zip 包，内含：`info.txt`、音频、封面、谱面 JSON。

info.txt 多个谱面用 `#` 分隔，单谱面格式：

```text
#
Name: 曲名
Path: 谱面ID
Song: 音频文件名
Picture: 封面文件名
Chart: 谱面JSON文件名
Level: IN Lv.14
Composer: 曲师
Charter: 谱师
Illustration: 画师
```

- `Level` 格式：`EZ/HD/IN/SP/AT Lv.X`；EZ≤10、HD≤14、IN≤17、SP 常为 `Lv.?`。
- `Song/Picture/Chart` 必须与包内实际文件名一致。音频 `.mp3`/`.ogg`、图片 `.jpg`/`.png` 均可，扩展名必须写实。
- 真实谱面的 info.txt 还会出现可选编辑器字段：`LastEditTime` / `Length`（音频秒数）/ `EditTime` / `Group`；省略不影响游玩，打包器只读取 Name/Path/Song/Picture/Chart/Level/Composer/Charter/Illustration。
