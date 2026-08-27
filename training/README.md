# 模型训练

代码先行、模型后训。全链路已经用合成检测器跑通，这里是把真模型接进去的那一步。

接进去之后只需要改一行配置：

```yaml
perception:
  detector: yolo        # synthetic → yolo
```

上层一行不用改——`IDetector` 抽象把这件事隔开了。

## 0　装依赖

```bash
pip install -r requirements-yolo.txt      # 约 2.5 GB，含 torch
```

## 1　数据集

方案书 §6.2.1 已经做过调研，结论是：**室内配电室的设备外观缺陷（渗漏油、
呼吸器变色、积水）几乎没有公开可用的标注数据，而设备状态量（表计、指示灯、
开关位置）有配套数据集。**识别对象因此定为三类状态量——这也是差异清单 A2
把 ICD 原定的 `OIL_LEAK` 换成 `SWITCH_HANDLE` 的理由。

| 用途 | 数据集 | 规模 | 许可 |
|---|---|---|---|
| 检测（三类状态量） | Roboflow `distribution_room` | 2773 张，检测框标注 | CC BY 4.0 |
| 指针与刻度分割 | PaddleX 工业表计读数数据集 | 检测 783 / 分割 414 | 百度官方，直链下载 |

**为什么要第二份**：指针与刻度的像素级分割标注是实现真正读数（而不是仅检出
表计位置）的必要条件，公开数据里只有 PaddleX 那一份提供。不过本项目的读数
算法走的是几何解算（椭圆拟合 + 极坐标展开），不依赖分割模型——这份数据主要
用来做**读数精度的交叉验证**：拿真实表盘图跑一遍 `read_pointer_gauge`，看
误差是否与合成场景上的量级一致。

```bash
python -m training.prepare_dataset --list          # 看要下什么、放哪
python -m training.prepare_dataset --check         # 检查已下载的数据完整性
python -m training.prepare_dataset --to-yolo       # 转成 YOLO 目录结构
```

Roboflow 需要账号（免费）拿下载链接；PaddleX 那份是直链。脚本不会替你绕过
任何授权，`--list` 会把地址和放置路径打印出来，手工下载后再跑 `--to-yolo`。

## 2　训练

两个阶段两个模型，对应 ICD 的两级级联：

```bash
python -m training.train_detector --stage cruise    # YOLO11s，巡航态，保召回
python -m training.train_detector --stage verify    # YOLO11m，复核态，判准
```

| | 巡航态 | 复核态 |
|---|---|---|
| 模型 | YOLO11s（3.2→9.4 M 参数） | YOLO11m（20.1 M） |
| 置信度阈值 | 0.25 | 0.60 |
| 目标 | **不漏**（漏检率 ≤2 %） | **判准** |
| 时延要求 | ≤100 ms/帧 | 车已停稳，放宽 |

两级级联的分工是整套方案的立论：一级把阈值压到 0.25 保召回，必然带来误报；
复核把误报消解掉。所以**训练时不要用同一套超参**——巡航态应当往高召回调
（低 `conf`、高 `iou` NMS），复核态往高精度调。

## 3　L3 异常检测

```bash
python -m training.train_anomaly           # EfficientAD，只用正常样本
```

非监督方法，**不需要缺陷标注数据**——这正好绕开了 §6.2.1 那条卡死外观缺陷的
数据可得性约束，也是差异清单 A2 换类之后仍能覆盖"纯 L1／未知异常"通路的
办法。

权重未就位时系统自动退回 `StatisticalAnomaly`（在线学习正常分布，零训练），
全链路照常跑。

## 4　量化与上板

RK3576 的 NPU 走 RKNN。这一步要等板子到位：

```bash
python -m training.export_rknn --weights runs/cruise/weights/best.pt
```

导出前先在 PC 上用 FP32 权重验证精度，再看 INT8 量化掉了多少。ICD 的
`DetectionEvent.model.quant` 字段就是为了让这件事在报文里可追溯——答辩时
要能说清"这条结论是哪个模型、什么量化跑出来的"。

## 5　登记模型版本

训完把版本登记到云端台账，把"哪一版模型产生了这条结论"钉住：

```bash
curl -X POST http://127.0.0.1:8000/api/models \
  -H 'Content-Type: application/json' \
  -d '{"version":"yolo11s-v1","stage":"CRUISE","dataset":"distribution_room",
       "metrics":{"mAP50":0.87,"recall":0.98},"activate":true}'
```

或直接在台账网页的「模型版本」页登记。
