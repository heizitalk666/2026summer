# artifacts/ —— 供复现的产物

| 文件 | 内容 | 必须 |
|---|---|---|
| `l3_report.json` | `train_anomaly` 打印的那份 info | ✅ **有坑，见 `../避坑清单.md` 第 4 节** |
| `rknn_export.md` | 导出记录：FP32/INT8 两组数、掉点、校准集来源 | ✅ |
| `l3.onnx` | 导出的权重 | 太大就写进 `where.txt` |
| `samples.txt` | 对比用的样本清单（哪些是正常、哪些是异常、各多少张） | ✅ 见 `../避坑清单.md` 第 1 节 |

**权重文件太大就别提交**（`docs/交付物清单.md:36`），放网盘给链接，
在 `where.txt` 里写清楚在哪。
