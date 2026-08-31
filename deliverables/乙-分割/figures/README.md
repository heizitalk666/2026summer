# figures/ —— 进 PPT 的图

**要求**（`docs/交付物清单.md` 总原则）：PNG，**能单独看懂**，坐标轴有标注。
组长拿到之后要能直接粘进 PPT，所以别交需要口头解释才看得懂的图。

| 文件名 | 内容 | 怎么产出 | 进 PPT 哪一页 |
|---|---|---|---|
| `mask_check.png` | 合成掩膜叠回原图的核对图 | `gen_synthetic --preview 8` | 数据质量页 |
| `paddlex_check.png` | PaddleX 掩膜叠回原图 | `--from-paddlex` 的 `check/` 目录 | 数据质量页 |
| `iou_compare.png` | numpy 基线 vs U-Net 的针 IoU | 自己画 | 方案比选页 |
| `reading_error.png` | 几何法 vs 学习法，按像素密度分档 | `bench_models --only reading` | **读数精度页（核心）** |

`paddlex_check.png` 是**验收硬指标**，不是可选项：类别映射错一位，
后面所有 IoU 都是错的且不报错，叠加图是唯一能当场看出来的地方。

`reading_error.png` 的画法有坑，见 [`../补齐清单.md`](../补齐清单.md) 第 3、4 节
——默认 `n=24` 的噪声比要比的差异还大。
