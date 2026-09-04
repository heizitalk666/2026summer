# L1 检测（巡航 / 复核两级 YOLO） 交付

> 状态：**巡航级已训完，复核级未训**。带 `⬜` 的是还要填的格子。
> 数字由组长从 `artifacts/stage_meta_cruise.json` 与 `results_cruise.csv` 誊入，
> **甲请核对**。文件已从目录根挪进 `figures/` 与 `artifacts/`（原先散在根目录）。
> 模板出处：[`docs/交付物清单.md`](../../docs/交付物清单.md) §一页纸模板。
> 这一页要直接粘进 PPT，**保持一页**；长的东西放 [`避坑清单.md`](避坑清单.md)。
>
> 分工书里甲这一节：`docs/后续计划与分工.md:169-197`。
> **这是三条路里唯一的硬缺口，也是关键路径**——乙丙都能靠合成数据先跑，甲不行。

## 一句话结论

⬜ 待甲写。手上已有的材料：巡航级 `yolo11s` 训 120 轮，验证集
mAP50 **0.9949**、mAP50-95 **0.7513**、precision 0.9967、recall 0.9976。
**还缺**：单帧耗时、复核级模型、切换前后对比——这三样齐了结论才写得出来。

> ⚠ mAP50 0.99 需要一句解释，答辩一定会问。见 [`避坑清单.md`](避坑清单.md) 第 7 节。

## 关键数字

| 模型 | mAP50 | 召回 | 漏检率 | 单帧耗时 | 达标 |
|---|---|---|---|---|---|
| 巡航 `yolo11s` | **0.9949** | **0.9976** | ⬜（≤ 2 %） | ⬜（≤ 33 ms） | mAP ✅ / 耗时未测 |
| 复核 `yolo11m` | ⬜ | ⬜ | ⬜ | ⬜ | **未训** |

巡航级补充：mAP50-95 **0.7513**、precision **0.9967**、120 轮、batch 4、imgsz 640、
预训练权重起训（`artifacts/args_cruise.yaml`）。

**增广对比**（任务书要求的方案比选证据）：

| 训练集 | mAP50 |
|---|---|
| 只用公开集 | ⬜ |
| 公开集 + 合成集 | ⬜ |

> `DetectionEvent.model.name` 的枚举**只有** `yolo11s` / `yolo11m`
> （`patrol/schemas/detection_event.schema.json:74-75`）。要报别的名字得走
> `validate.py` 的 `ALLOWED_DRIFT` 流程，**不许静默塞值**。

## 最重要的一项：切换前后对比

**这一条比 mAP 更重要**，因为它证明「代码先行、模型后训」这条路走通了。

| | 合成检测器 | 真权重 | 结论 |
|---|---|---|---|
| 证据包数 | ⬜ | ⬜ | |
| 平均密度比 | ⬜ | ⬜ | |
| 真缺陷组 Δconf | ⬜ | ⬜ | |
| 复核成功率 | ⬜ | ⬜ | |

**只要不退化就是成功**，不需要变好。

> ⚠ **每边至少跑三轮取区间，不要只跑一轮。** `run_all` 的复核成功率本身就在
> 50 %–100 % 之间浮动（`docs/新手上路.md:170`；我实测过两轮，83.3 % 与 75.0 %）。
> 单轮对比分不清「退化」和「噪声」。
>
> ⚠ **切到 yolo 之后像素密度的来源变了**，这是这张表最容易被误读的地方，
> 详见 [`避坑清单.md`](避坑清单.md) 第 1 节。

## 图

| 文件 | 内容 | 从哪来 | 进 PPT 哪一页 | 状态 |
|---|---|---|---|---|
| `figures/results_cruise.png` | 巡航级训练曲线 | `training/runs/<stage>/` ultralytics 自动生成 | 训练过程页 | ✅ |
| `figures/confusion_matrix_cruise.png` | 巡航级混淆矩阵（另有 `_normalized_` 归一化版） | 同上 | 识别效果页 | ✅ |
| `figures/BoxPR_curve_cruise.png` | 巡航级 PR 曲线（另有 P / R / F1 三条） | 同上 | 识别效果页 | ✅ |
| 复核级的同名三张 | 复核级 `yolo11m` 的训练曲线 / 混淆矩阵 / PR | 同上 | 同上 | ⬜ **模型未训** |

> 图的文件名带 `_cruise` 后缀，是按训练阶段区分的——复核级训出来之后按
> `_verify` 后缀放进同一个目录即可，一页纸不必再改结构。

## 怎么复现

```bash
python -m training.prepare_dataset --check       # 先确认数据齐了，全绿再往下
python -m training.prepare_dataset --to-yolo    # 转成 YOLO 格式
python -m training.prepare_dataset --check-leak # 查 train/val 有没有增广副本串台

python -m training.train_detector --stage cruise --device ⬜   # 巡航模型
python -m training.train_detector --stage verify --device ⬜   # 复核模型

# 切进链路：configs/system.yaml → perception.detector: yolo
python -m patrol.tools.run_all --seconds 300
```

**`--device` 三个人各占一张卡**（`0`、`1`，多卡 `0,1`，没卡 `cpu`），
别都用默认的——会互相抢显存。

## 未做 / 未验证

1. **复核级 `yolo11m` 未训。** 验收标准是 mAP50 较巡航级 +≥5 点，
   但巡航级已经 0.9949，**这条标准在当前数据集上没有余量**——
   见 [`避坑清单.md`](避坑清单.md) 第 7 节，需要换个说法。
2. **单帧耗时未测**（限值 ≤ 33 ms）。这是巡航级能否 30 Hz 跑的硬指标。
3. **漏检率未测**（限值 ≤ 2 %）。
4. **切换前后对比未做**——`perception.detector: yolo` 还没切进链路跑过
   `run_all`。**这一项比 mAP 更重要**，理由见本页上文。
5. **增广对比表未做**（只用公开集 vs 公开集+合成集），任务书要的比选证据。
6. 训练时试过 `--resume` 但脚本不支持（`train_detector.py` 没有这个参数）。
   不影响结果，但如果需要断点续训，得先给脚本加上。
