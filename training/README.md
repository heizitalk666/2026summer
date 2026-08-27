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

### 公开数据集和合成数据，各干什么

仓库里有两条造数据的路（`prepare_dataset.py` 与 `gen_synthetic.py`），
**不是二选一，分工是明确的**：

> **公开数据集是训练的主力。合成数据只补三样公开数据结构上给不了的东西。**

先说为什么公开数据必须是主力：真实的纹理、光照、背景杂物、镜头畸变、传感器
噪声——合成数据在这几维上再怎么做域随机化也差得远。拿合成集训出来的权重直接
上真机，sim-to-real gap 会把指标吃掉一大截。所以 **L1 检测的权重应当从
`distribution_room` 训，不要从合成集训。**

再说合成数据补的是哪三样：

1. **按像素密度分层的样本。**整套方案的立论是"5 m 处 1× 只有 50 px 读不准，
   要停车变焦到 120 px"。而公开数据集是摄影师怎么取景就怎么是的，没有"像素
   密度"这一维的控制。要训练/评测"密度不够时别硬猜"，就必须能指定密度——
   合成器里这是一行参数（先抽目标密度、再反解需要多大变焦）。
2. **同一目标的 before/after 配对。**复核增益指标（Δconf、密度比）比的是
   *同一块表*在两个倍率下的两张图。公开数据集里没有这种配对，也没法事后配出来。
3. **表面文字的真值。**OCR 互证要知道"这块表印着的量程到底是多少"才能评测。
   公开集只标框，不标"盘面上印着什么"。

分割那一路要特别说一句：**`prepare_dataset.py --from-paddlex` 才是主路。**
公开数据里只有 PaddleX 那一份给了像素级的指针标注，而合成掩膜训出来最弱的
一环恰恰就是"针 vs 刻度"（实测针的验证 IoU 只有 0.182）——那正是真实标注最
该派上用场的地方。两边类别对不齐要处理：

| | background | face | needle | ticks |
|---|---|---|---|---|
| 本项目 | 0 | 1 | 2 | 3 |
| PaddleX | 0 | **无** | 1 (pointer) | 2 (scale) |

PaddleX 没有盘面这一类。它的图是表盘紧裁剪，那些"背景"像素大半其实就是盘面，
但边角又确实是真背景，分不开。所以默认映射成 255（忽略），这些像素不进损失
函数。分工于是清楚了：**针与刻度的区分从真实数据学，盘面与背景的区分从合成
数据学。**

```bash
# 分割：真实标注为主
python -m training.prepare_dataset --from-paddlex training/datasets/meter_seg
python -m training.train_segmenter --data training/datasets/seg_paddlex

# 合成集做增广 / 覆盖密度分层与 before-after 配对
python -m training.gen_synthetic --n 400 --out training/datasets/synth
```

两个目录结构完全同构（`images/ masks/ labels/` 三件套），可以直接合起来训。

**一个必须说清的限制**：PaddleX 那两条直链我这边下不到（沙箱网络策略挡了
`bj.bcebos.com`，网关返回 403，不是站点的问题）。所以 `--from-paddlex` 的
用例是拿照着官方文档搭出来的假目录测的——它证明的是"转换逻辑对得上文档描述
的结构"，不是"在真数据上跑通了"。真包下下来之后**务必人眼看几张
`check/` 里的叠加图**：掩膜错位在任何数字上都看不出来，只有画出来才看得见。
如果目录名对不上，脚本会把它找过哪些名字打印出来，改个名再跑即可。

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
