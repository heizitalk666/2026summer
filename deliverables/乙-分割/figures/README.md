# figures/ —— 进 PPT 的图

**要求**（`docs/交付物清单.md` 总原则）：PNG，**能单独看懂**，坐标轴有标注。
组长拿到之后要能直接粘进 PPT，所以别交需要口头解释才看得懂的图。

| 文件名 | 内容 | 怎么产出 | 进 PPT 哪一页 |
|---|---|---|---|
| `mask_check.png` | 合成掩膜叠回原图的核对图 | `gen_synthetic --preview 8` | 数据质量页 |
| `paddlex_check.png` | PaddleX 掩膜叠回原图 | `--from-paddlex` 的 `check/` 目录 | 数据质量页 |
| `iou_compare.png` | numpy 基线 vs U-Net 的针 IoU | 自己画 | 方案比选页 |
| `reading_error.png` | 几何法 vs numpy 基线 vs U-Net，P90 按像素密度分档 | `bench_models --only reading --n 200 --json ...` | **读数精度页（核心）** |

`paddlex_check.png` 是**验收硬指标**，不是可选项：类别映射错一位，
后面所有 IoU 都是错的且不报错，叠加图是唯一能当场看出来的地方。

`reading_error.png` 已按 [`../补齐清单.md`](../补齐清单.md) 第 3、4 节重画：
n=200、纵轴改 **P90**（不再是 n=24 的中位数）、30–60/60–90 两档标灰、误差棒
取 bootstrap 95% CI。数据来自 `bench_models --json` 的落盘，`make_figures.py`
不再硬编码数字。`mask_check.png` 图例也修了：叠加图是针红刻度蓝，图例之前
写成针蓝刻度橙（BGR 常量直接当 RGB），现已转对。
