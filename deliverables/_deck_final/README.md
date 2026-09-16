# 最终答辩 PPT 的生成脚本

成品是 [`deliverables/最终答辩.pptx`](../最终答辩.pptx)（25 页）。这里放的是把它生成出来的脚本，
改数字、改措辞都改这里再重出，**不要直接在 PowerPoint 里改**——下次重出会覆盖掉手改的部分。

| 文件 | 作用 |
|---|---|
| `final.py` | 全部 25 页的版式与文字。stage A 从学院模板复制 25 页骨架，stage B 用 python-pptx 重写正文 |
| `notes.py` | 每页的讲稿，写进 PPT 备注页 |
| `figs_final.py` | 重画两张图：重复性分布 `repeat_final.png`、检测样例 `l1_samples.png` |
| `img/` | 页面上用到的图片 |
| `render.ps1` | 用 PowerPoint 把每页导成 PNG，用来核对版式 |

## 重出

```bash
# 只重出正文（骨架已在本目录，30 MB，不进版本库）
python deliverables/_deck_final/final.py b

# 连骨架一起重建：需要学院模板与 pptx skill 的 unpack/add_slide/pack
#   模板默认取仓库根目录的 C424 PPT模板-2024新.pptx，也可用 C424_TEMPLATE 指定
#   skill 脚本默认取 ~/.claude/skills/pptx/scripts，也可用 PPTX_SKILL 指定
python deliverables/_deck_final/final.py
```

核对版式（需要装了 PowerPoint 的 Windows）：

```powershell
powershell -File deliverables/_deck_final/render.ps1 -In deliverables/最终答辩.pptx -OutDir out/deck_png
```

## 几条踩过的坑

1. **模板的页码与日期是字段，缓存值还是模板里的 13 / 2026-01-12。**WPS 和缩略图直接用缓存值，
   所以 `fix_footer` 把页码写成实际值、把日期字段换成纯文本。
2. **目录页与结束页的页脚压在照片上**，`fix_photo_footers` 把它们去掉或改成深蓝。
3. **数字和单位之间要用不换行空格**（`glue()`），否则「22 / 43 ms」这种会在行尾被拆开。
4. 每页的数都要能在 [`docs/指标汇总表.md`](../../docs/指标汇总表.md) 里找到出处。**表里没有的数不要往上写。**
