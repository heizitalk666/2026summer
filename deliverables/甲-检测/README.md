# 甲 · L1 检测 交付

## 一句话结论

两级 YOLO（巡航 yolo11s 120 轮 / 复核 yolo11m 80 轮，均掺合成渲染 ×3 微调 15 轮）在无泄漏真实集上 mAP50 = 0.994、漏检率 0.2%，接进全链路后复核成功率 100%（7/7）、L2 读数全部产出；Δconf +0.007 未达 ICD 的 +0.25——原因不是模型而是指标口径（详见下文），已如实记录待组长裁决。

## 关键数字

| 指标 | 值 | 限值/基线 | 达标 |
|---|---|---|---|
| 巡航 yolo11s mAP50（真实 val 404 帧） | 0.9941 | — | ✅ |
| 巡航召回 / 漏检率 | 0.9983 / **0.17 %** | 漏检 ≤2 % | ✅ |
| 复核 yolo11m mAP50（同一 val） | 0.9941 | — | ✅ |
| 复核召回 / 漏检率 | 0.9982 / **0.18 %** | 漏检 ≤2 % | ✅ |
| 数据泄漏（--check-leak 跨基名） | 0 | 0（泄漏版 mAP 0.995→无泄漏 0.994，虚高已挤掉） | ✅ |
| 复核成功率（run_all 5.5 min，只计本轮） | **100 %**（7/7） | >85 % | ✅ |
| L2 读数产出 | 7/7（3 READING_ABNORMAL + 4 READING_OK） | 此前 0（UNKNOWN_ANOMALY） | ✅ |
| 真缺陷 Δconf | +0.0072 | >+0.25 | ❌（口径问题，见下） |
| 复核像素密度比 | 2.2–2.9 × | >2 | ✅ |
| 单帧推理 @1280（1660 Ti） | ~15 ms | ≤100 ms 节拍 | ✅ |

### 关于 Δconf 未达 +0.25 的说明（重要，不粉饰）

ICD 的 Δconf 增益模型假设**巡航态不确定**（合成基线：远景 ~0.4 → 复核确认 ~0.95，Δ≈+0.5）。
实测证据包：巡航置信度 0.85–0.93、复核 0.90–0.92——真实模型在远景就足够自信，
Δconf 的数学上限只剩 ~0.1，**继续训模型无法突破**。复核的真实增益体现在：

1. **误报抑制**：FALSE_ALARM 组 0.93 → 0.0（复核变焦后框都检不出，压掉）
2. **读数判定**：READING_ABNORMAL 3 例（真缺陷实锤）/ READING_OK 4 例（可疑被证伪）
3. **像素密度 2.2–2.9 倍**（目标从 ~50 px 放大到 ~120 px）

建议组长裁决：Δconf 指标按真模型重新校准（如"复核后仍 ≥0.9 且 L2 读数一致"替代），或以 1–3 作为复核增益的验收口径。

## 增广对比（任务书方案比选证据）

| 训练集 | 真实 val mAP50 | 虚拟渲染域巡航检出 | 结论 |
|---|---|---|---|
| 只用真实公开集 | 0.994 | **0 %**（探针实测，域差距） | 单独不可用 |
| 真实 + 合成渲染 ×3 微调 | 0.994（不退化） | 77–100 %（三类稳定检出） | **采用** |

合成集：`training/datasets/yolo_synth`（165 帧，ZOOMS 1.0–3.0× 五档，标注取渲染器真值）。

## 切换前后对比（run_all --seconds 330 同口径）

| | 合成检测器 | 真权重（yolo） | 结论 |
|---|---|---|---|
| 证据包数 | 7 | 7 | 同量级 |
| 复核成功率 | 100 % | **100 %** | 不退化，达标 |
| 真缺陷 Δconf | +0.0130 | +0.0072 | **合成基线也够不到 +0.25**，坐实口径问题 |
| 密度比（真缺陷组） | 2.75 | 2.91 | 一致 |
| L2 读数 | 7/7 | 7/7 | 一致 |

> 注：合成检测器的 Δconf 同样 ≈ 0——它的设计增益（巡航 0.4 → 复核 0.95）在当前
> 场景的像素密度/变焦配置下没有兑现。ICD Δconf>+0.25 的目标在两条检测器上都
> 不成立，属指标校准问题而非检测器问题。

## 图

![巡航训练曲线](figures/cruise_results.png)
巡航 yolo11s 120 轮损失与 mAP 曲线（无泄漏数据集）。

![巡航混淆矩阵](figures/cruise_confusion_matrix.png)
三类目标（PRESSURE_GAUGE / INDICATOR_LIGHT / SWITCH_HANDLE）几乎无混淆。

![复核 PR 曲线](figures/verify_PR_curve.png)
复核 yolo11m 80 轮，三类 PR 曲线。

其余见 `figures/`（verify_results / verify_confusion_matrix / cruise_PR_curve）。

## 交付物清单

| 文件 | 说明 |
|---|---|
| `artifacts/cruise/best.onnx`（36.6 MB） | 巡航模型，imgsz=1280，opset 12，冒烟通过 |
| `artifacts/verify/best.onnx`（77.1 MB） | 复核模型，imgsz=1280，opset 12，冒烟通过 |
| `training/runs/cruise_ft/weights/best.pt` | 巡航部署权重 |
| `training/runs/verify_ft/weights/best.pt` | 复核部署权重 |
| `training/runs/{cruise,verify}*/stage_meta.json` | 指标溯源（conf 阈值、NMS、mAP/P/R） |
| `configs/system.yaml` | detector: yolo，两级权重路径已接 |
| `figures/` | 训练曲线 / 混淆矩阵 / PR 曲线 ×2 级 |

## 怎么复现

```bash
# 1. 数据：真实集 YOLO 化 + 无泄漏划分（--check-leak 验零跨基名）
python -m training.prepare_dataset --to-yolo
python -m training.prepare_dataset --check-leak

# 2. 巡航基线（真实集 120 轮）
python -m training.train_detector --stage cruise --device 0

# 3. 合成渲染集 + 混合微调（域差距修复，真实 val 守 0.994）
python -m training.gen_yolo_renders
python -m training.train_detector --stage cruise --data training/datasets/yolo_mixed/data.yaml --weights training/runs/cruise/weights/best.pt --name cruise_ft --epochs 15 --device 0

# 4. 复核模型（yolo11m 80 轮，中断可 --resume）+ 同配方微调
python -m training.train_detector --stage verify --device 0 --epochs 80
python -m training.train_detector --stage verify --data training/datasets/yolo_mixed/data.yaml --weights training/runs/verify/weights/best.pt --name verify_ft --epochs 15 --device 0

# 5. 导 ONNX（imgsz=1280 对齐部署）+ 当场冒烟
python -m training.export_onnx --detector training/runs/cruise_ft/weights/best.pt --out-dir artifacts/cruise --imgsz 1280
python -m training.export_onnx --detector training/runs/verify_ft/weights/best.pt --out-dir artifacts/verify --imgsz 1280

# 6. 全链路验收（success rate 100% 那轮的口径）
python -m patrol.tools.run_all --seconds 330
```

## 未做 / 未验证

- **RKNN 上板**：无板子，导出链到 ONNX 冒烟为止（丙的分工范围）。
- **INT8 量化实测**：system.yaml 标 INT8 是部署意图，掉点数据待上板后由量化校准（校准集必须取 `evidence/` 本场景图，不用 COCO）。
- **OCR 互证通路**：`rapidocr_onnxruntime` 未装，L3 OCR 互证降级为 off（不影响 L2 读数与本表结论）。
- **单帧节拍**：仍有个别帧 105–138 ms 超 100 ms 节拍（yolo11m @1280 于 1660 Ti），复核态允许放宽，巡航态由 yolo11s 承担；板上 NPU 的余量待实测。
- **Δconf 口径**：见上文，待组长裁决后如需改 `patrol/common/messages.py` 的增益定义，另行提交。
