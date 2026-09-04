# 基于 RK3576 边缘计算的无人车主动式 AI 巡检系统

配电室巡检机器人。巡航时低成本地"扫一遍"，发现可疑目标就**停车、对准、变焦、
再看一眼**——这一步把表盘从 50 像素放大到 120 像素以上，读数才够得着 0.5 % FS
的精度要求。这是本项目和"拍一路视频回去慢慢看"的根本区别。

**硬件还没到，但整套系统现在就能在笔记本上跑起来。**虚拟配电室按针孔投影渲染，
所以像素密度、读数精度、PID 阶跃响应这些指标现在就能测出真数，不是推导值。
硬件到位后改两处配置即可切到真机。

---

## 5 分钟跑起来

```bash
git clone https://github.com/heizitalk666/2026summer.git
cd 2026summer

python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt                        # 约 80 MB，不需要 GPU
```

> **第一次来的人先读 [`docs/新手上路.md`](docs/新手上路.md)**——每一步都写了
> 「你应该看到什么」和「看不到时查哪一行」。需要 Python 3.10 以上。
>
> 网页版（可勾选、记进度）：
> <https://claude.ai/code/artifact/8daf04a9-17b7-423d-92d6-ad43cde83093>

然后按顺序跑这四条，每一条都有明确的预期输出：

```bash
# 1. 接口定义与代码是否还对得上（57 项）。这条不过就别往下走
python -m patrol.tools.validate

# 2. 看一眼虚拟配电室，出一张变焦对比图
python -m patrol.tools.viewer --demo-zoom --out out/

# 3. 跑全部测试（约 1 分钟）
pytest -q

# 4. 起全系统跑一轮巡检，跑完出小结，浏览器开 127.0.0.1:8000 看台账
python -m patrol.tools.run_all --seconds 300
```

第 2 条产出的 `out/zoom_compare.png` 是全项目最直观的一幕：同一块压力表，
巡航态 50.0 px（指针读不出来），3× 变焦后 150.0 px（刻度清晰可读）。
公式算出来是 49.8 / 149.5，吻合到 0.4 %。

第 4 条跑完应当看到类似这样的小结（数值每轮略有浮动）：

```
证据包 6 个，复核成功率 100.0 %（目标 > 85 %）

结论                      条数      平均Δconf        平均密度比
READING_ABNORMAL             1       0.4452       2.2311
READING_OK                   5       0.4458       2.2835

真缺陷组 Δconf 均值 = 0.4452（目标 > +0.25）
```

> 没有显示器的机器（服务器 / WSL 无 X）把 `opencv-python` 换成
> `opencv-python-headless`，除预览窗口外功能不受影响。

---

## 先读哪份文档

| 想知道什么 | 看这里 |
|---|---|
| **每个文件是干嘛的**、一次复核的完整数据流 | [`docs/代码地图.md`](docs/代码地图.md) |
| **剩下四周谁做什么**、验收标准与风险 | [`docs/后续计划与分工.md`](docs/后续计划与分工.md) |
| **第一次上手、装环境、跑通** | [`docs/新手上路.md`](docs/新手上路.md) |
| **怎么把这套系统演给人看**（三块屏幕、支线演示、出图） | [`docs/演示指南.md`](docs/演示指南.md) |
| 系统怎么搭的、四个进程怎么通信 | [`docs/架构说明.md`](docs/架构说明.md) |
| **四类 AI 模型怎么配合把识别做好** | [`docs/多模型协同.md`](docs/多模型协同.md) |
| 每一步命令怎么跑、出什么 | [`docs/操作步骤.md`](docs/操作步骤.md) |
| 每个设计决定背后的理由 | [`docs/设计思想.md`](docs/设计思想.md) |
| 接口定义（冻结基线） | [`docs/ICD-RK3576-PATROL-v2.0.md`](docs/ICD-RK3576-PATROL-v2.0.md) |
| **D3 评审议程**：方案书与 ICD 的 22 处出入 | [`docs/一致性差异清单-方案书-ICD-v1.0.md`](docs/一致性差异清单-方案书-ICD-v1.0.md) |
| 交给硬件组的串口约定 | [`docs/底盘串口协议.md`](docs/底盘串口协议.md) |
| 数据集与训练流程 | [`training/README.md`](training/README.md) |

---

## 目录

```
patrol/
  common/       双时间戳、run_id/event_id、ZeroMQ 封装、报文构造与 Schema 校验
  schemas/      五份 JSON Schema（从 ICD 附录 D 抽出）
  scene/        虚拟配电室：针孔光学、世界模型、表计绘制、渲染器
  drivers/      IChassis / IPTZ / ICamera / ILocalizer 四个抽象基类
    stub/         桩：主动注入真机上会出现的麻烦
    real/         串口 / V4L2
    factory.py    全仓库唯一的 driver_mode 分支点
  perception/   四路模型：L1 检测 / L2 分割读数 / L2' OCR 互证 / L3 异常
    detector/     检测：合成 / YOLO / ONNX
    segment/      分割：几何（默认）/ 逐像素分类器 / ONNX
    ocr/          OCR：RapidOCR（自带权重、离线）/ 停用
    reading/      指针、指示灯、开关、铭牌语义与互证
    fusion.py     L4 显式仲裁：四路证据 → 六种结论 + 逐条理由
  mission/      十状态机、云台伺服、抑制规则、复核预算
  gateway/      五项校验、参数范围硬编码、心跳看门狗、审计日志
  uploader/     证据包组装、断点续传、云端上报
  tools/        validate / viewer / calibrate / tune_pid / run_all / fakecar
                console（指令实时流）/ bench_models（四路横向对比）/ textdraw
cloud/          FastAPI + SQLite 台账、人工复核、模型版本登记
configs/        system / scene / stub / waypoints / camera / real
training/       合成数据集生成、检测/分割/异常训练、ONNX 与 RKNN 导出
tests/          505 条用例（389 个测试函数，参数化展开后 505）
```

---

## 四个可以现场演示的东西

方案书说安全设计的说服力不在于声明，在于能当场演示。这四条都能当场跑：

> 完整的演示流程（三块屏幕怎么摆、每块说什么、支线演示、给 PPT 出图）见
> [`docs/演示指南.md`](docs/演示指南.md)。

```bash
# 越界指令被拒，并留下逐项校验的审计记录
pytest tests/test_gateway.py -k out_of_range -v

# 杀掉 AI 进程后，网关看门狗让车自己走完路线
python -m patrol.tools.run_all &
pkill -f patrol.mission.node

# 注入安全事件，正在进行的复核 200 ms 内中止
pytest tests/test_fsm.py -k safety -v

# 关掉增益调度，3× 变焦下超调从 0.9 % 变成 47.6 %
python -m patrol.tools.tune_pid --out out/pid --compare-gain-schedule
```

---

## 硬件到位后要改什么

**两处，上位机代码一行不动。**

```yaml
# configs/system.yaml
driver_mode: real          # stub → real

# configs/real.yaml
real.serial.chassis.port: /dev/ttyUSB0    # 端口名、波特率、云台限位、相机设备号
```

真车到之前可以先用假小车把串口链路调通（它说 `docs/底盘串口协议.md` 定义的
同一套协议，并复用桩的故障注入）：

```bash
python -m patrol.tools.fakecar --pty       # POSIX：打印 /dev/pts/N
python -m patrol.tools.fakecar --tcp       # Windows：打印 tcp://127.0.0.1:5760
# 把打印出来的那一行填进 configs/real.yaml 的 real.serial.chassis.port，
# 再把 configs/system.yaml 的 driver_mode 改成 real
pytest tests/test_serial_protocol.py tests/test_fakecar_tcp.py -v   # 不需要任何设备
```

**Windows 上必须用 `--tcp`**：`os.openpty()` 是 POSIX 专有的，`--pty` 会直接
失败（不给参数时按平台自动选，照着敲哪条都不会踩坑）。TCP 环回保住了"假小车
是独立进程、字节真的过内核"这个关键性质，分帧、CRC、超时、重传、2 % ACK 丢包
注入全部照原样发生；换掉的只是承载。**它不能替代物理层**——没有波特率、没有
线路噪声、没有帧错误，TCP 还保证有序不丢。所以它证明协议栈与时序逻辑正确，
不证明电气特性。

---

## 把指令直接显示出来

没有硬件时，这三个面就是"车"和"云台"唯一看得见的样子。一份数据源，三处
说的话一定一致。

```bash
python -m patrol.tools.run_all --console     # 系统 + 终端指令流水（推荐）
python -m patrol.tools.viewer --live         # 预览窗口，画面上叠加指令
# 浏览器 http://127.0.0.1:8000/ 的「实时」页：指令流水 + 配电室俯视图
```

```
[t=   80.7s] → 小车   ✓ 暂停 VERIFY_REQUEST                       1.5 ms
[t=   83.3s] → 云台   ✓ 转到 pan=+110.0° tilt=+2.7° zoom=1.00×    1.6 ms
[t=   84.7s] → 云台   ✓ 转到 pan=+111.1° tilt=+2.7° zoom=2.59×    0.9 ms
[t=   86.9s] → 小车   ✓ 恢复巡航                                    0.9 ms
```

被拒的指令标红，并打出六项校验里是哪一项没过——这是三层安全边界唯一可
观测的地方。

---

## 当前状态

| 项目 | 状态 |
|---|---|
| 边缘端四进程 + 云端台账 | 全链路跑通，复核成功率 100 % |
| 读数精度 | 基本误差 0.469 % FS、线性度 0.267 % FS（限值 0.5 / 0.4）达标 |
| 重复性 | **0.321 % FS，压线超差**（限值 0.3），受检测框噪声支配，见下 |
| 云台控制 | 3× 变焦下超调 0.9 %、调节时间 1.10 s（限值 10 % / 1.5 s）达标 |
| 识别 | 四路模型（检测 / 分割 / OCR / 异常）+ 显式仲裁全部在跑，见 [`docs/多模型协同.md`](docs/多模型协同.md) |
| OCR 互证 | 已在跑真模型（RapidOCR，离线自带权重）；实测 90 px 以上可读，误判冲突全档为 0 |
| 合成数据集 | 检测框 / 分割掩膜 / OCR / L3 正常集一次产出，掩膜与图像逐像素对齐 |
| 测试 | 505 条用例，`validate` 57 项全绿 |
| YOLO 权重 | 接口与加载逻辑就位，默认走合成检测器，等训练 |
| RKNN 上板 | 导出脚本就位，等板子 |

**重复性这一项目前压线超差，而且逐次运行会在限值上下浮动。**它受合成检测器
注入的检测框抖动支配，不是读数算法本身的极限。这一条应当写进 D3 评审的待办，
不建议调参数把它凑过去——那等于把误差藏起来。
