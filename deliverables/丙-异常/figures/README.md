# figures/ —— 进 PPT 的图

**要求**：PNG，**能单独看懂**，坐标轴有标注。

| 文件名 | 内容 | 进 PPT 哪一页 |
|---|---|---|
| `score_dist.png` | 正常 vs 异常的异常分分布，两个直方图叠一起 | 异常检测页 |
| `compare.png` | 统计法 vs EfficientAD | 方案比选页 |

`score_dist.png` 是这一路最好讲的一张图——**两堆分数分不分得开，一眼就看出来**。
记得画上阈值线，纵轴标清是频数还是密度。统计法的参照值：
正常最大 1.5σ、异常 ≥6σ（`patrol/perception/anomaly.py:84`）。
