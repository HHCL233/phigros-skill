---
name: phigros-skill
description: 为音乐编写 Phigros 谱面（RPE JSON 与 .pez 打包）。Use when the user asks 写谱、做谱、chart a song、生成 RPE 谱面、修改或校验现有 RPE 谱面、打包 pez. Do not use for 官谱格式（# 行文本格式）解析.
---

# Phigros 写谱（RPE 格式）

为一段音乐制作 Phigros 谱面。只输出 RPE（Re:PhiEdit）JSON 格式，**不支持官谱格式**。

## 参考文档（按需读取，不要一次全读）

| 文档 | 内容 | 何时读 |
| --- | --- | --- |
| [references/charting.md](references/charting.md) | 采音、难度标尺、真实谱面画像、手感红线、表演规范、生成顺序 | 步骤 2 起 |
| [references/configs.md](references/configs.md) | 配置库 A~J + 表演手法库 P~S（排键与演出的推荐素材库） | 步骤 3 |
| [references/rpe-format.md](references/rpe-format.md) | 格式规范全文（字段、事件、control 数组、时间系统） | 步骤 4 |
| [references/easing.md](references/easing.md) | 缓动编号速查 | 步骤 3 |

## 工作流

1. **收集信息**：确认音乐文件路径。若无 BPM 信息，运行
   `python3 scripts/bpm_detect.py <音乐文件>`（在 skill 目录下执行），并与用户确认最终 BPM 与 offset
   （毫秒；suggested_offset_ms 可直接用作 META.offset）。同时确认：目标难度（EZ/HD/IN/AT，EZ≤10、HD≤14、IN≤17）、
   曲名/曲师/谱师等元数据、是否需要表演。
2. **分段设计**：按 intro / verse / build-up / drop / chorus / bridge / outro 列时间轴，为每段定采音对象与密度（见 charting.md）。
   **采音必须基于完整 dB 矩阵**：先运行 `python3 scripts/spectrum.py <音乐文件>` 生成 `<名>_spectrogram.npz`
   （完整 dB 矩阵，采音的唯一数值依据）、频谱图 PNG（仅看整体轮廓）与逐时段主频 csv（仅粗定位）；
   用 numpy 读取 npz 的 `db/freqs/times`，按 BPM/offset 的拍网格统计低频/中频/高频能量与谱通量，取 onset 峰作为打点候选
   （数值方法见 charting.md「采音」第 2 步；可用 `--start/--end` 分段导出以控制文件大小）。
   需要逐片响度/起音/基频时再运行 `python3 scripts/segment_analyze.py <音乐文件>`（默认 0.2 秒一片，输出 json+csv）。
   某段旋律看不清/听不准时，用 `python3 scripts/melody.py <音乐文件> --start <秒> [--end <秒>]` 直接输出该段旋律
   （音名序列 + 音符表，缺省 --end 表示到音频结尾）；低音重的段落抬高 `--band-lo/--fmin` 聚焦旋律区。
   **旋律/音域/时值走向可用于判段落情感**（音高上行=推进紧张、下行+长音=回落抒情、音域整体偏高=明亮亢奋、
   同音反复偏低=压抑蓄力），据此定该段密度与演出幅度；判据表见 charting.md「采音」第 5 步。
   读图与采音步骤见 charting.md「采音」；不得无频谱依据地排键。
3. **排键与表演**：配置**推荐优先使用** configs.md 配置库（A~J），表演**推荐优先使用**手法库（P~S），可按实际情况调整参数或新增配置；手感红线见 charting.md，缓动见 easing.md。
   键型骨架用 `python3 scripts/gen_config.py --config <编号> --start <TimeT> --end <TimeT> --x <首音x> --div <几分>`
   生成（只输出 notes JSON，写进哪条判定线由 AI 决定）。
   **生成结果只是骨架，必须按采音逐音微调**：挪拍位贴合 onset、改 x/类型、镜像、增删音、换拍位避免配置重复，
   并按歌曲对应位置的节奏疏密调整细分（密句 16 分、疏句降为 8 分/4 分，一个乐句内也可切换，不全程固定一个细分），
   不得原样写入交付；微调后仍需过校验。
4. **生成谱面**：
   - 新谱先用 `python3 scripts/scaffold.py --name <曲名> --song <音频文件名> --background <封面文件名> --id <谱面ID> --level "IN Lv.14" --composer <曲师> --charter <谱师> --bpm <BPM> --out-dir <输出目录>` 生成骨架（含 info.txt 模板）。
   - 按 rpe-format.md 的字段规范编辑 JSON 写入音符与事件。时间用 TimeT 三元组 `[拍, 分子, 分母]`；
     秒 ↔ TimeT 换算一律运行 `python3 scripts/time_conv.py`（`--to-beat` / `--to-sec`），禁止手算。
5. **校验**：`python3 scripts/validate_chart.py <chart.json> --audio <音频文件>`。必须修复全部 ERROR；
   带 `--audio` 时会同时查音符是否晚于音频总时长（ERROR）。校验不通过不得交付。
6. **打包**：把 info.txt、音频、封面、chart JSON 放入同一目录并核对 info.txt 字段与实际文件名一致，然后
   `python3 scripts/package_pez.py <目录> -o <曲名>.pez`（打包前会复核音符不晚于音频总时长，越界则拒绝打包）。
7. **交付**：报告 note 数、时长、各段配置概览，建议用户在 RPE 编辑器或 PhiZone Player 试玩微调 offset。

## 硬性格式规则（违反即谱面损坏，详见 rpe-format.md）

- `TimeT = [beat, num, den]` 表示 `beat + num/den` **拍**（1 拍 = 60/BPM 秒）；第一位是拍，**不是小节**。`den > 0`，`0 ≤ num < den`。
- 音符 `type`：1=Tap（蓝）、2=Hold（长条）、3=Flick（红）、4=Drag（黄）；非 Hold 的 `endTime` 必须等于 `startTime`。
- 每条线 `notes` 按「非 Hold 在前、Hold 在后」分组，组内按时间升序；自建谱 `numOfNotes` 必须等于 notes 长度。
- `eventLayers` 为 1~8 层数组（常用 1~4 层）；最终事件值 = 各层之和，层内同类事件按时间升序。
- `speedEvents` 无 `bezier/easingLeft/easingRight/easingType` 字段；普通事件 `easingType` 0~49（自建谱用 0~29）。
- 每条线必须带五个 control 数组（alphaControl/posControl/sizeControl/skewControl/yControl）与 `extended.inclineEvents`（默认一条 0 值事件）。
- 坐标范围 x ∈ [-675, 675]、y ∈ [-450, 450]；x 网格全曲统一一套：75 系（0、±75、±150、±225、±300、±375、±450、±525）、67.5 系（0、±67.5、±135、±202.5、±270、±337.5、±405）或可视区等分点（±168.75、±337.5、±506.25）。

## 排键与表演红线（scripts/validate_chart.py 自动检查）

- **采音必须基于完整 dB 矩阵**：跑 `scripts/spectrum.py` 后用 `_spectrogram.npz` 的 `db/freqs/times`，按拍网格算频段能量与谱通量定打点；PNG 只能看轮廓、`_peaks.csv` 只能粗定位，不得作为唯一依据，更不得凭听感或整体音量排键。
- 排键**推荐优先使用** configs.md 配置库（A~J）的配置；可按实际情况调整参数（x/拍位/方向/镜像/步进）或新增配置。
- **排键整齐**：全曲统一一套 x 网格（75 系 / 67.5 系 / 自定义对称网格），段内步进规整、左右对称；
  音符 x 大量落在网格外时校验器给 WARNING（乱序坐标少用，配置库 B2 仅在用户要求该风格时用）。
- **谱面不单调（重复部分少）**：同一配置/手法连续原样复读不超过 2~3 次，之后必须变型（换 x / 镜像 / 换拍位 / 换配置 / 换线 / 换音型）；
  禁止整段（≥8 拍）原样复制，二次副歌等重复乐段必须做变奏；手段见 charting.md「避免单调」。
- **不乱用 16 分配置**：十六分/三连十六分/三十二分**只放在频谱起音密集的乐句**，疏句降回八分/四分（或留白）；
  禁止整段无差别铺 16 分、全曲默认 16 分、用 16 分连发凑密度；同一 16 分配置连跑约 8 拍即须换细分/音型或留呼吸
  （F1/F2/F4、J1/J6 等 16 分配置按起音密度裁剪，不能整段照搬）。
- **禁止 4 押及以上**：同刻**不同 x** 的可判音符最多 3 个；3 押只限高潮段零星点缀。
  重音改用双押、3 押、同位叠判定（同刻同 x 叠不同类型，只算 1 押，不受此限）或 Drag。
- 同一判定线同一 eventLayer 内相同时间不要有两个同类型事件。
- **禁用假音符（isFake 恒为 0）**：一律不写假音符，0 速线也不放音符；演出用判定线运动/alpha/缓动/文字（手法库 P~S 为推荐手法）。
- 音符线 speedEvents ≥10 速（10~12 常用）；**不使用 note 绑线**（0 速线不放任何音符）。
- 每条线至少一条 alphaEvents（0~255）；表演线（无可判音符的线）alpha ≤200。
- 可判音符最早 `[1,0,1]`，开场留纯表演，不要一开头就进 note。
- **音符不得超出音频总时长**：任何音符（含 Hold 结束时刻）都不得晚于音乐总时长；校验器带 `--audio` 时报 ERROR，打包时复核。
- 主线落点：正打 y≈-300（下方），倒打（rotate 180）y≈+300（上方）；不停在 y=0（默认 y=0，用 moveYEvents 移到位）。
- Hold `endTime ≥ startTime`；同键相邻音符间隔 ≥1/32 拍。
- 长条末尾同 x 不放 Tap（Drag/Flick 接续允许）。
- 同刻 Tap-Tap、同刻 Tap-Hold、Hold 期间 Tap-Hold，|Δx| 均 >50（同位叠判定不受此限）。
- **多押内音符不要靠太近**：同刻 ≥2 可判音符按屏幕坐标（含跨线）两两间距 >50；完全重合的叠判定（≤3）除外。
- Drag 后 1/4 拍内不接 |Δx| < 50 的 Tap（错开 x 或拉开间隔均可）。

## 风格偏好（校验器不检查）

- 先定难度档：1~10 低密度（EZ）、10~13 中密度（HD~IN13）、14~15 高密度（IN14~15）、16~17 超高密度（IN16~17）；交界处先与用户确认。
- 密度随段落与情感起伏，不做恒定密度；全曲不能全是密集 note，段落划分必须明显；重复乐段必须变奏，不做原样复制。
- 少用双押海（单段 ≤4 组）与纵连（单段 ≤6 连）；3 押只在高潮段使用，少而精；**4 押及以上一律不用**。
- 复杂表演推荐优先用 configs.md 手法库（P~S）；可按曲风调整参数或新增手法。
- 表演线最少 5 条（scaffold `--lines` 默认 6 = 1 主 + 5 表演）；主线最好有与歌曲情感和段落相对应的移动（moveY 与 rotate），无幅度限制。
- 判定线移动（moveX/moveY/rotate）尽量不用 1 号 linear，除承载音符的音符线外；表演线与主线过渡用 2/6 等缓动。
- 采音依据频谱等音乐信息（onset、低频鼓点能量、人声/旋律频段轮廓）；BPM 未确认前不要开始排键。
- 双手配置注意左右手分配；表演服务于音乐，不为炫技牺牲可读性。
- 写谱前对照 charting.md「真实谱面画像」，使密度与难度档对齐。
