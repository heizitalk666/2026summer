# 中期答辩 PPT 的源

```bash
cd deliverables
npm install pptxgenjs      # 只需一次
node make_deck.js          # → deliverables/中期答辩.pptx
```

## 文件

| 文件 | 内容 |
|---|---|
| `../make_deck.js` | 入口：设版面（**LAYOUT_WIDE 必须在 addSlide 之前设**）、按顺序拼五节 |
| `theme.js` | 配色、版式与可复用组件。**页码与章节号自动递增**，见 `slide()` 与 `secNo()` |
| `s1_intro.js` | 1–7：封面 / 目录 / 课题 / 像素密度 / 复核流程 / 架构 / 无硬件 |
| `s1b_arch.js` | 8–11：技术路线四个决定 / 十二步数据流 / 接口冻结机制 / 代码分层 |
| `s1c_scale.js` | 12–14：工作量总览 / 三项检查 / **完成度总表** |
| `s2_models.js` | 15–25：识别层 / L4 仲裁 / 甲 L1 / 乙 L2 / 丙 L3 / 三路小结 |
| `s2b_results.js` | 26–28：甲分类表现 / 丙误报漏报归因 / 证据包与复核增益 |
| `s3_system.js` | 29–35：安全 / 云台 / 端到端 / 方法论 / 风险 / 下一步 / 结束 |
| `img/` | 嵌入的图。三个人的交付图 + 本地生成的 zoom / thirdperson / PID 阶跃 |

**页码不用手工维护。**所有页必须用 `T.slide(pres)` 创建，`T.foot(s, "说明")`
不再传页码，`T.head(s, "auto", ...)` 自动递增章节号。插页删页都不用改别处，
只有第 2 页目录里的页码区间要跟着改。

## 明天上午补齐甲、乙

两处槽位都在 `s2_models.js`，搜 `slot(` 就能找到：

- **第 13 页** 甲 · L1 待补：复核级 mAP、单帧耗时、漏检率、增广对比、`--check-leak` 结果
- **第 16 页** 乙 · L2 待补：核心图 `reading_error.png` 重画（n≥200、纵轴 P90）

补齐做法：把新图放进 `img/`，把对应的 `slot(...)` 换成正常卡片或 `s.addImage(...)`，
然后重跑 `node make_deck.js`。页码是硬编码在每页 `T.foot(s, ..., N)` 里的，
**增删页要顺手改页码**。

## 改完必跑

```bash
node make_deck.js
python3 <pptx-skill>/scripts/office/validate.py 中期答辩.pptx     # 文件结构
python3 <pptx-skill>/scripts/office/soffice.py --headless --convert-to pdf 中期答辩.pptx
pdftoppm -jpeg -r 100 中期答辩.pdf slide                          # 逐页看有没有溢出/重叠
```

沙箱里 LibreOffice 默认只装了 core，要 `apt-get install libreoffice-impress poppler-utils`
才能转 PDF 和出图。

## 数据口径

页面上的数字**全部是本次实测**，不用文档旧值。两处与旧文档不一致的地方：

- PID：README 旧值 0.9 % / 1.10 s / 47.6 %；本次实测 **3.0 % @1× · 1.0 % @3× · 关调度 37.7 %**
- 端到端复核成功率：不写「100 %」，如实写 **75–83 %**（未达 85 % 目标），并说明主因
