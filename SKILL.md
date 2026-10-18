---
name: phigros-skill
description: 为音乐编写 Phigros 谱面（RPE JSON 与 .pez 打包）。Use when the user asks 写谱、做谱、chart a song、生成 RPE 谱面、修改或校验现有 RPE 谱面、打包 pez. Do not use for 官谱格式（# 行文本格式）解析.
---

# Phigros 写谱（RPE 格式）

为一段音乐制作 Phigros 谱面。只输出 RPE（Re:PhiEdit）JSON 格式，**不支持官谱格式**。

## 工作流

1. **收集信息**：确认音乐文件路径。若无 BPM 信息，运行
   `python3 scripts/bpm_detect.py <音乐文件>`（在 skill 目录下执行）得到候选 BPM 与起始偏移，
   向用户确认最终 BPM 与 offset（毫秒，正值 = 谱面相对音乐延后，bpm_detect 的 suggested_offset_ms 可直接用作 META.offset）。
   同时确认：目标难度（EZ/HD/IN/AT，EZ≤10、HD≤14、IN≤17）、曲名/曲师/谱师等元数据、是否需要表演（判定线演出）。
2. **分段设计**：把歌曲按 intro / verse / build-up / drop / chorus / bridge / outro 列出时间轴，
   为每段确定采音对象（鼓点、人声、旋律）与密度。格式规范见 [references/rpe-format.md](references/rpe-format.md)。
3. **排键与表演**：按难度选取配置（交互、纵连、双押、拆线、绑线等），规划判定线数量与演出。
   手感与配置准则见 [references/charting.md](references/charting.md)，缓动选择见 [references/easing.md](references/easing.md)。
4. **生成谱面**：
   - 新谱先用 `python3 scripts/scaffold.py --name <曲名> --song <音频文件名> --background <封面文件名> --id <谱面ID> --level "IN Lv.14" --composer <曲师> --charter <谱师> --bpm <BPM> --out-dir <输出目录>` 生成骨架（同时生成 info.txt 模板）。
   - 然后直接编辑 JSON 写入音符与事件。时间一律用 TimeT 三元组 `[拍, 分子, 分母]`；
     音乐秒数 → TimeT、TimeT → 秒的换算一律运行 `python3 scripts/time_conv.py`（`--to-beat` / `--to-sec`），
     禁止手算，防止 4 倍或 BPM 段累计错误。
5. **校验**：`python3 scripts/validate_chart.py <chart.json>`。必须修复全部 ERROR；WARNING 酌情处理。
   校验不通过的谱面不得交付。
6. **打包**：把 info.txt、音频、封面、chart JSON 放入同一目录并核对 info.txt 各字段与实际文件名一致，然后
   `python3 scripts/package_pez.py <目录> -o <曲名>.pez`。
7. **交付**：报告 note 数、时长、各段配置概览，并建议用户在 RPE 编辑器或 PhiZone Player 试玩微调 offset。

## 硬性格式规则（违反即谱面损坏）

- 时间 `TimeT = [beat, num, den]`，含义为 `beat + num/den` **拍**（1 拍 = 四分音符 = 60/BPM 秒）。第一位是拍，**不是小节**——按小节理解会差 4 倍。`den > 0`，num 取 `0 <= num < den`。
- 换算：`秒 = 拍 × 60/BPM + offset/1000`；`拍 = (秒 − offset/1000) × BPM/60`。多 BPM 段须分段累计。**秒↔拍换算一律用 scripts/time_conv.py，禁止手算。**
- 音符 `type`：1=Tap，2=Hold，3=Flick，4=Drag。非 Hold 的 `endTime` 必须等于 `startTime`。
- 每条判定线的 `notes` 按"非 Hold 在前、Hold 在后"分组，组内按时间升序；自建谱面 `numOfNotes` 必须等于 notes 长度（外部谱面常见不一致，校验器仅警告）。
- 空判定线可省略 `notes` 字段（示例谱面即如此）。
- `eventLayers` 为 1~8 个层级的数组（常用 1~4 层，RPE 1.7 导出可见 5 层或含 null 元素）；最终事件值 = 各层之和。层内同一类事件按时间升序。
- 普通事件含 `bezier/easingLeft/easingRight/easingType` 等字段；`speedEvents` 没有这些字段，且 start 与 end 一般相同。
- `easingType` 普通事件取 0~49（0~29 为经典缓动，30~49 为 RPE 1.5+ 扩展；自建谱面优先用 0~29 保证兼容）；bezier 事件用 `bezier: 1` + `bezierPoints: [x1,y1,x2,y2]`。extended 占位事件的 easingType 可被编辑器写成更大值（惰性不生效，勿模仿）。
- 每条判定线必须带五个 control 数组（alphaControl/posControl/sizeControl/skewControl/yControl），默认两点写法见 format 文档。
- `extended` 至少包含 `inclineEvents`（默认一条 0 值事件即可）。
- 屏幕坐标：x ∈ [-675, 675]，y ∈ [-450, 450]。x 常用网格（全曲统一一套）：75 系（0、±75、±150、±225、±300、±375、±450、±525）、67.5 系（0、±67.5、±135、±202.5、±270、±337.5、±405）或可视区等分点（±168.75、±337.5、±506.25）；双押常用 ±135、±270、±337.5、±405。

## 质量要求

- **排键只能使用 charting.md「配置库」整理的配置**（A 单点多押 / B 交互 / C 纵连 / D 楼梯 / E Hold / F Drag 流 / G Flick / H 结构手法），可替换拍位、x、方向或镜像复用；**不得自创库外配置**——确需新配置时先向用户提议，确认写入配置库后再使用。
- **先定难度档再排键**：1~10 低密度（EZ）、10~13 中密度（HD~IN13）、14~15 高密度（IN14~15）、16~17 超高密度（IN16~17），档位要点见 charting.md「难度标尺」；密度对齐所在档位，交界处从严或从宽先与用户确认。
- 复杂表演优先使用 charting.md「复杂表演手法库」整理的手法（P 线组织 / Q 线运动 / R 音符演出 / S 文字氛围），库外手法先提议、确认入库后再用。
- **每条判定线的 eventLayers 至少要有一条 alphaEvents 事件**（alpha 值 0~255；起始用 `[beat,0,1]→[曲末,255]` 或 [0,0,1] 覆盖全曲），不做显隐演出也要写一条恒定 alpha；校验器对缺失给 WARNING。
- **表演判定线（无可判音符的线）alpha 不得大于 200**，保持背景感、不抢音符主体；淡入淡出演出峰值也不超过 200；音符线不受此限。校验器对违反给 WARNING。
- 采音必须卡音乐实际节奏点：**依据频谱等音乐信息**定位打点（起音 onset、低频鼓点能量、人声/旋律频段轮廓），而不是只凭该段落的整体音量与情感；BPM 未确认前不要开始排键。
- **开头不出 note**：可判音符最早放在 beat `[1,0,1]`（1:0/1）或之后；且**不建议一开头就进 note**——开场应做纯表演（判定线汇聚、标题卡、道具演出，见手法库 Q1/S1/P3），最好专门留出一段时间只做演出，再进第一个 note。校验器对早于 [1,0,1] 的可判音符给 WARNING（假音符除外）。
- **承载音符的判定线 speedEvents 至少 10 速**（10~12 常用，保证下落可读）；例外是特殊情况——如 0 速绑线放音符做表演（合法手法，需自行处理显示/隐藏）。校验器会对速度 <10 的音符线给出 WARNING，表演线可忽略。
- 多线分工：1 条（组）主音符线承载可判音符；**表演判定线没有数量上限，但最少 5 条**（scaffold `--lines` 默认 6 = 1 主 + 5 表演；绑定道具、装饰音符、文字/遮罩演出都放表演线），保持主可读性。主线可以运动和旋转，但幅度不宜太大（短时间内旋转不超过 50°）。
- **双押海可以使用**；避免的是 4k/5k 式段落（3 押及以上的固定轨道堆叠、对称满屏多押）**时间过长或段落过多**：单段不超过约 16 拍，全曲少量点缀即可，其余用错位、交互、拖动链条、Flick 收尾与留白。**3 押及以上（堆叠或散点）只允许在高潮段（Drop/Chorus/Build-up 冲顶处）使用**，其余段落重音用双押/同位叠判定/交互/Drag。校验器对过长/过多的高密度多押段落给出 WARNING。
- 主音符线的纵向落点：**正打（音符向下落）主线应停在屏幕下方 y≈-300；倒打（线 rotate 180、音符向上落）主线应停在上方 y≈+300**；不要让主线停在屏幕中间（y=0）。默认线在 y=0，需用 moveYEvents 移到位。
- Hold 结束时间不得早于开始时间；同键位相邻音符要留出可判定的间隔（一般 ≥ 1/32 拍）。
- **长条末尾同 x 不放 Tap**：Hold（长条）endTime 时刻的同 x 位置不要放 Tap（松手瞬间同点再点手感极差）；Drag/Flick 接续长条末尾是允许的常规手法。校验器对违反给 WARNING。
- **长条与 Tap 同时间距离不要过近**：Hold 持续期间不要在其 |Δx| < 100 范围内放 Tap。校验器对违反给 WARNING。
- **不要 Drag 后紧跟 x 相近的 Tap**：Drag 之后 1/4 拍内不要接 |Δx| < 50 的 Tap（黄键划完立刻同位点击手感冲突；错开 x 或拉开间隔均可）。校验器对违反给 WARNING。
- 双手配置注意左右手分配，避免同手连续高速 16 分双押。
- 表演（拆线/绑线/变速/淡入淡出）服务于音乐，不要为炫技牺牲可读性。
- 写谱前先对照 charting.md 的"真实谱面画像"（九张实测谱 IN Lv.13~AT16 的平均 NPS、押数分布、类型配比），使密度与所选难度档对齐，不要超密度。
