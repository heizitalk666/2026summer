# 中期答辩 PPT 的源

```bash
cd deliverables
npm install pptxgenjs      # 只需一次
node make_deck.js          # → deliverables/中期答辩.pptx
```

## 文件

| 文件 | 内容 |
|---|---|
| `../make_deck.js` | 入口：设版面（**LAYOUT_WIDE 必须在 addSlide 之前设**）、按顺序拼三节 |
| `theme.js` | 配色、版式与可复用组件（页眉圆环、卡片、统计块、表格） |
| `s1_intro.js` | 第 1–9 页：封面 / 目录 / 课题 / 像素密度 / 复核流程 / 架构 / 无硬件 / 工作量 / 质量 |
| `s2_models.js` | 第 10–20 页：识别层 / L4 仲裁 / 甲 L1 / 乙 L2 / 丙 L3 / 小结 |
| `s3_system.js` | 第 21–27 页：安全 / 云台 / 端到端 / 方法论 / 风险 / 下一步 / 结束 |
| `img/` | 嵌入的图。三个人的交付图 + 本地生成的 zoom / thirdperson / PID 阶跃 |

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
