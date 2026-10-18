# Phigros Skill — 用 AI 编写 RPE 谱面

一个面向 AI 编码助手（Trae 等）的 Skill：为音乐编写 **Phigros 风格的 RPE（Re:PhiEdit）谱面**，从 BPM 检测、排键、表演事件到打包 `.pez` 一条龙完成。

> 仅支持 RPE JSON 格式。不支持官方谱面（`#` 行文本格式）的解析。

## 功能

- **完整工作流**（SKILL.md 定义 7 步）：接收音频 → BPM/offset 检测 → 难度确认 → 生成骨架 → 排键 → 校验 → 打包 `.pez`
- **RPE 格式规范**（references/rpe-format.md）：TimeT 时间系统、事件系统、音符规则，均经真实谱面验证
- **配置库**（references/charting.md）：从真实谱面归纳的键型配置白名单（单点/双押、交互、纵连、楼梯、Hold、Drag 流、Flick 等 30 条，A~H 编目），AI 只允许使用库内配置
- **缓动手册**（references/easing.md）：RPE easingType 0~29 的行为与选用建议
- **工具脚本**（scripts/）：
  - `scaffold.py` — 生成谱面骨架（chart JSON + info.txt，默认 1 主音符线 + 5 表演线）
  - `validate_chart.py` — 格式校验（ERROR 硬性拦截 / WARNING 提示表演手法）
  - `package_pez.py` — 打包 `.pez`（zip 格式，含 info.txt/音频/曲绘/chart）
  - `bpm_detect.py` — BPM 与 offset 估计（谱通量 + 自相关）
  - `time_conv.py` — 秒 ↔ TimeT 换算唯一入口（多 BPM 段、吸附）
- **真实谱面回归基线**：校验器对多张真实 RPE 谱面保持 0 ERROR

## 安装

把本目录复制到 AI 助手的 skills 目录：

```bash
# 项目级（仅当前项目可用）
cp -r phigros-skill /path/to/your/project/.trae/skills/

# 全局（所有项目可用）
cp -r phigros-skill ~/.trae-cn/skills/   # 按你的助手实际全局 skill 目录调整
```

安装后在对话中直接说「写谱 / 做谱 / chart 这首歌」即可触发。

## 使用示例

```
用户：为这首歌写一张 IN Lv.14 的谱面（附 song.mp3）
AI：  bpm_detect 估计 BPM/offset → 与你确认 → scaffold 生成骨架 →
      按配置库排键 → validate 校验 → package_pez 打包 → 输出 .pez 与统计报告
```

## 设计要点

- **配置白名单**：AI 排键只能使用配置库整理的配置，不得自创；需要新配置时先向用户提议、确认入库后使用
- **4k/5k 约束**：双押海允许；3 押及以上堆叠段落有长度/占比上限（押数按同刻不同 x 计，同位叠判定只算 1 押）
- **手感红线**：同 x 相邻判定最小间隔、Hold 期间同 x 限制、Flick 方向规则等
- **校验哲学**：ERROR 只拦格式损坏；WARNING 提示表演手法（真实谱面大量合法偏离，不升为 ERROR）

## 免责声明

- 本项目仅供学习与技术研究。Phigros 游戏及原谱版权归 [Pigeon Games（鸽游网络）](https://pigeongames.com/) 所有；RPE（Re:PhiEdit）为社区谱面编辑器。
- 使用时请自备音频、曲绘等素材，并遵守其授权条款；如参考他人谱面，请尊重原谱师的署名与许可。
- 本 skill 不分发任何真实谱面素材。

## License

MIT
