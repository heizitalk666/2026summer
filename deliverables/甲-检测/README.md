# L1 检测（巡航 / 复核两级 YOLO） 交付

> 状态：**空模板，待甲填**。带 `⬜` 的是要填的格子。
> 模板出处：[`docs/交付物清单.md`](../../docs/交付物清单.md) §一页纸模板。
> 这一页要直接粘进 PPT，**保持一页**；长的东西放 [`避坑清单.md`](避坑清单.md)。
>
> 分工书里甲这一节：`docs/后续计划与分工.md:169-197`。
> **这是三条路里唯一的硬缺口，也是关键路径**——乙丙都能靠合成数据先跑，甲不行。

## 一句话结论

⬜ 一句话。例："公开集 2773 张训出的巡航模型 mAP50 0.xx、单帧 xx ms，
切进链路后 `run_all` 三轮的复核成功率与合成检测器持平，说明代码先行、
模型后训这条路走通了。"

## 关键数字

| 模型 | mAP50 | 召回 | 漏检率 | 单帧耗时 | 达标 |
|---|---|---|---|---|---|
| 巡航 `yolo11s` | ⬜ | ⬜ | ⬜（≤ 2 %） | ⬜（≤ 33 ms） | ⬜ |
| 复核 `yolo11m` | ⬜ | ⬜ | ⬜ | ⬜ | ⬜（较巡航 +≥5 点） |

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
| `figures/results.png` | 训练曲线 | `training/runs/<stage>/` ultralytics 自动生成 | 训练过程页 | ⬜ |
| `figures/confusion_matrix.png` | 混淆矩阵 | 同上 | 识别效果页 | ⬜ |
| `figures/PR_curve.png` | PR 曲线 | 同上 | 识别效果页 | ⬜ |

## 怎么复现

```bash
python -m training.prepare_dataset --check      # 先确认数据齐了，全绿再往下
python -m training.prepare_dataset --to-yolo    # 转成 YOLO 格式

python -m training.train_detector --stage cruise --device ⬜   # 巡航模型
python -m training.train_detector --stage verify --device ⬜   # 复核模型

# 切进链路：configs/system.yaml → perception.detector: yolo
python -m patrol.tools.run_all --seconds 300
```

**`--device` 三个人各占一张卡**（`0`、`1`，多卡 `0,1`，没卡 `cpu`），
别都用默认的——会互相抢显存。

## 未做 / 未验证

⬜ 老实写，没有就写「无」。
