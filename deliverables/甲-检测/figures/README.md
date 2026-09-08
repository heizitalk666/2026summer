# figures/ —— 进 PPT 的图

**要求**：PNG，**能单独看懂**，坐标轴有标注。别交需要口头解释才看得懂的图。

| 文件名 | 内容 | 怎么产出 | 进 PPT 哪一页 |
|---|---|---|---|
| `results.png` | 训练曲线 | `training/runs/<stage>/` ultralytics 自动生成，直接拷 | 训练过程页 |
| `confusion_matrix.png` | 混淆矩阵 | 同上 | 识别效果页 |
| `PR_curve.png` | PR 曲线 | 同上 | 识别效果页 |

两级模型各一套，文件名加后缀区分，例如 `results_cruise.png` / `results_verify.png`。
