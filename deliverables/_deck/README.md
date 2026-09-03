# 中期答辩 PPT 的源

```bash
cd deliverables
npm install pptxgenjs      # 只需一次
node make_deck.js          # → deliverables/中期答辩.pptx
```

## 文件

按学术答辩五段式组织，一个分节一个文件。

| 文件 | 内容 |
|---|---|
| `../make_deck.js` | 入口：设版面（**LAYOUT_WIDE 必须在 addSlide 之前设**）、按顺序拼六个模块 |
| `theme.js` | 配色、版式与组件：`slide()` `head()` `foot()` `card()` `table()` `divider()` `slot()`。**页码与章节号自动递增** |
| `newpages.js` | 五段式补充的五页：现状分析、研究目标与结果、资料来源、创新点、参考文献 |
| `sec0_cover.js` | 1–2：封面、目录 |
| `sec1_bg.js` | 3–7：**一、研究背景和现状** |
| `sec2_flow.js` | 8–13：**二、研究思路和结构** |
| `sec3_method.js` | 14–33：**三、方法和研究内容** |
| `sec4_sum.js` | 34–40：**四、总结与创新点** |
| `sec5_ref.js` | 41–45：**五、参考文献与存在的不足** |
| `img/` | 嵌入的图。三个人的交付图 + 本地生成的 zoom / thirdperson / PID 阶跃 |

**页码不用手工维护。**所有页必须用 `T.slide(pres)` 创建，`T.foot(s, "说明")` 不传页码，
`T.head(s, "auto", ...)` 自动递增章节号。插页删页都不用改别处，
只有 `sec0_cover.js` 目录里的页码区间要跟着改。

**分节页**用 `T.divider(pres, "一", "节名", "本节回答……", [要点数组])`，
建议每节 5 条要点，4 条会留白偏多。

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
