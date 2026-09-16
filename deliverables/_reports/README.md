# 两份实训报告的生成脚本

成品：

- [`小组报告-基于RK3576边缘计算的无人车主动式AI巡检系统设计.docx`](../小组报告-基于RK3576边缘计算的无人车主动式AI巡检系统设计.docx)（30 页）
- [`个人报告-吴明哲.docx`](../个人报告-吴明哲.docx)（23 页）

两份都是在学院模板 `测控系统综合实训小组报告模板.docx` 上填出来的（个人报告模板与它逐字节相同），
封面版式、页眉页脚、分节、标题自动编号、摘要与参考文献样式全部沿用模板。

## 交稿前要自己填的（文档里都是黄色高亮）

小组编号、专业年级、答辩日期的“日”、三位组员的姓名与学号、四个人的工作比例。
改完把高亮去掉（Word：选中 → 开始 → 突出显示 → 无颜色）。

## 重出

```bash
python deliverables/_reports/build_group.py      # 小组报告
python deliverables/_reports/build_personal.py   # 个人报告
```

`rep_lib.py` 是填充工具，`content_group.py` / `content_personal.py` 是内容。
改数字只改 content 文件，**所有数都要能在 [`docs/指标汇总表.md`](../../docs/指标汇总表.md) 里找到出处**。

生成出来的目录是一个 Word 目录域，第一次打开时按 Ctrl+A 再按 F9 更新，页码才是实际值
（用 Word 另存或导 PDF 时会自动更新一次）。

## 图从哪来

`fig/` 里：`fig_arch/fsm/safety/evidence/scene/models/ptz/l3/deploy/active.png` 是答辩 PPT
对应页去掉页眉页脚后的截图（PPT 的生成脚本在 [`../_deck_final/`](../_deck_final/)），
`fig_repeat/pid/calib/l1*.png` 是标定与训练产出的图。要改图先改 PPT 或重画，再重新裁剪。
