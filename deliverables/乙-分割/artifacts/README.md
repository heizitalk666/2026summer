# artifacts/ —— 供复现的产物

| 文件 | 内容 | 必须 |
|---|---|---|
| `pixel.npz` | `train_segmenter` 产出，内含 `val_iou` | ✅ |
| `train_unet.py`（或你自己的文件名） | **U-Net 训练代码** | ✅ 仓库里没有，非交不可 |
| `unet.onnx` | 导出的权重 | 太大就写进 `where.txt` |
| `split.json` 或 `split.txt` | train/val 划分（哪些文件进了 val） | ✅ 见 `../补齐清单.md` 第 2 节 |

**权重文件太大就别提交**（`docs/交付物清单.md:36`），放网盘给链接，
在 `where.txt` 里写清楚在哪。
