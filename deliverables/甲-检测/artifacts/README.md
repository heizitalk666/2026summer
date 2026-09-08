# artifacts/ —— 供复现的产物

| 文件 | 内容 | 必须 |
|---|---|---|
| `stage_meta_cruise.json` | `train_detector` 自动写出（`training/train_detector.py:78`） | ✅ |
| `stage_meta_verify.json` | 同上 | ✅ |
| `best_cruise.onnx` | 导出的巡航权重 | 太大就写进 `where.txt` |
| `best_verify.onnx` | 导出的复核权重 | 同上 |
| `switch_compare.md` | 切换前后各三轮的 `run_all` 小结原文 | ✅ 见 `../避坑清单.md` 第 2 节 |

**权重文件太大就别提交**（`docs/交付物清单.md:36`），放网盘给链接，
在 `where.txt` 里写清楚在哪。
