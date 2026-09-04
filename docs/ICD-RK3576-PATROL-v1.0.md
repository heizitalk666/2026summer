# RK3576 无人车主动式 AI 巡检系统 · 接口定义文档

| | |
|---|---|
| 文档编号 | ICD-RK3576-PATROL |
| 版本 | v1.0（M1 冻结版） |
| 状态 | 待评审 → 冻结 |
| 对应里程碑 | M1（D3）：四份 JSON Schema + 三个桩 + 驱动层抽象接口，全组评审通过 |
| 冻结日期 | D3 |
| 适用范围 | 四人组全部代码分支，桩环境与真机环境共用同一套接口 |

## 冻结规则

D3 评审通过后，本文档定义的字段名、枚举值、指令白名单、数值范围进入冻结状态。此后的修改按下列规则处理：

| 变更类型 | 举例 | 处理方式 |
|---|---|---|
| 新增可选字段 | `detection.attributes` 下加一个键 | `schema_version` 次版本号 +1，无需全组评审，通知即可 |
| 新增枚举值 | `defect_class` 加一类缺陷 | 同上，但需同步更新附录 B |
| 修改字段语义、类型、范围 | `pan_deg` 改用弧度 | 主版本号 +1，全组重新评审，三个桩同步改 |
| 增删白名单指令 | 加一条 `SET_SPEED` | 需要重新评审安全边界，默认不批准 |

接收方对未知的可选字段一律忽略，不报错。发送方不得依赖接收方处理未在本文档中出现的字段。

---

## 1. 系统接口拓扑

### 1.1 进程划分

系统在 RK3576 上跑四个进程，进程之间的所有通信都必须走本文档定义的接口，不允许通过共享文件或全局变量传数据。

| 进程 | 职责 | 是否触碰执行器 |
|---|---|---|
| `perception` | 一级巡航检测、二级复核推理、L2 读数、L3 异常检测 | 否 |
| `mission` | 复核状态机、复核预算调度、优先级排队 | 否，只发指令 |
| `gateway` | 安全网关：指令白名单校验、参数范围硬校验、心跳看门狗 | 是，唯一出口 |
| `uploader` | 证据包落盘、断点续传、云端上报 | 否 |

`perception` 与 `mission` 之间不经过网关，因为二者都不产生执行器动作。所有指向底盘和云台的动作只有 `gateway` 一个出口，这是安全边界第一层的物理保证。

### 1.2 接口清单

| 编号 | 名称 | 方向 | 传输层 | 频率 | Schema 文件 |
|---|---|---|---|---|---|
| IF-1 | `DetectionEvent` 感知事件 | `perception` → `mission` | ZeroMQ PUB/SUB，`ipc:///tmp/patrol_det` | 10 Hz（巡航）/ 按需（复核） | `detection_event.schema.json` |
| IF-2 | `ControlCommand` 控制指令 | `mission` → `gateway` → 执行器 | ZeroMQ REQ/REP，`ipc:///tmp/patrol_cmd` | 事件驱动 + 5 Hz 心跳 | `control_command.schema.json` |
| IF-3 | `StatusReport` 状态与安全上报 | `gateway` → `mission` / `perception` | ZeroMQ PUB/SUB，`ipc:///tmp/patrol_status` | 20 Hz 周期 + 事件插播 | `status_report.schema.json` |
| IF-4 | `EvidencePackage` 证据包 | `uploader` → 云端 | MQTT v3.1.1（元数据）+ HTTPS PUT（大文件） | 每次复核一包 | `evidence_package.schema.json` |

选 ZeroMQ 的理由：IPC 传输不占网络栈，PUB/SUB 天然支持一发多收（`StatusReport` 同时给 `mission` 和 `perception`），REQ/REP 强制每条指令必须有回执，正好匹配指令必须带 ACK 的要求。桩环境把 `ipc://` 换成 `tcp://` 就能跨机调试，代码不用改。

### 1.3 拓扑图

```
                    ┌──────────────┐
                    │  perception  │
                    │  一级+二级推理 │
                    └──────┬───────┘
                      IF-1 │ DetectionEvent
                           ▼
                    ┌──────────────┐   IF-3 StatusReport
                    │   mission    │◀────────────────┐
                    │  复核状态机   │                 │
                    └──────┬───────┘                 │
                      IF-2 │ ControlCommand          │
                           ▼                         │
                    ┌──────────────┐                 │
                    │   gateway    │─────────────────┘
                    │  白名单+范围校验 │
                    └──────┬───────┘
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌─────────┐  ┌─────────┐  ┌─────────┐
        │ IChassis│  │  IPTZ   │  │ICamera  │
        │ 底盘驱动 │  │ 云台驱动 │  │ 相机驱动 │
        └────┬────┘  └─────────┘  └─────────┘
             │ 独立于 gateway 与 AI
             ▼
        ┌─────────────┐
        │ 底盘安全层    │  可否决任何上层指令
        └─────────────┘

        ┌──────────┐  IF-4   ┌────────┐
        │ uploader │────────▶│  云端   │
        └──────────┘         └────────┘
```

底盘安全层画在驱动之下，表示它不在软件调用链上。它是底盘固件里的独立逻辑，网关下发的指令到达底盘之后仍然要过这一层，AI 侧和网关侧都没有绕过它的通路。

---

## 2. 通用约定

### 2.1 时间

每条报文带两个时间戳，用途不同，不可互相替代。

| 字段 | 类型 | 含义 | 用途 |
|---|---|---|---|
| `ts_mono_ns` | int64 | 单调时钟纳秒，`CLOCK_MONOTONIC` | 算时延、超时判定、时序对齐 |
| `ts_utc_ms` | int64 | UTC 毫秒 | 证据包时间标记、云端展示、日志检索 |

超时判定一律用 `ts_mono_ns`。系统时间在 NTP 同步时会跳变，用它算超时会在同步瞬间产生几百毫秒的假超时，正好落在安全响应的量级上。

### 2.2 标识符

| 字段 | 格式 | 生成方 | 生命周期 |
|---|---|---|---|
| `run_id` | `YYYYMMDD-HHMMSS-<4位随机>` | `mission` 启动时 | 一轮巡检 |
| `event_id` | UUIDv4 | `perception` | 一个可疑目标的完整复核过程 |
| `track_id` | int32，单轮内递增 | `perception` 的跟踪器 | 目标被连续跟踪的时段 |
| `cmd_id` | UUIDv4 | `mission` | 一条指令及其 ACK |
| `seq` | uint32，按通道各自递增，溢出回绕 | 各发送方 | 检测丢包 |
| `waypoint_id` | `WP-<两位序号>` | 标定阶段人工分配 | 整个项目 |

`event_id` 是串起四份 Schema 的主键。同一次复核里，`DetectionEvent`、由它触发的所有 `ControlCommand`、期间的 `StatusReport`、最终的 `EvidencePackage`，全部携带同一个 `event_id`。事后排查问题时按 `event_id` 过滤日志，能拿到完整的一条时间线。

### 2.3 坐标系

| 名称 | 定义 | 用在哪 |
|---|---|---|
| `map` | 建图时确定的固定 ENU 系，x 东 y 北 z 天，原点在起始充电位 | 巡检位坐标、车辆位姿 |
| `base_link` | 车体系，原点在后轴中心，x 车头方向，y 左，z 上 | 相对运动指令 |
| `camera` | 相机光心，x 右 y 下 z 前 | 目标方位解算 |
| `image` | 像素系，原点左上角，x 右 y 下，单位 px | 检测框 |

云台的 `pan` / `tilt` 相对于 `base_link` 定义，不是相对于 `map`。这样车体转向时不需要重算云台角度。

### 2.4 单位

| 物理量 | 单位 | 字段名后缀 |
|---|---|---|
| 长度、距离 | 米 | `_m` |
| 角度 | 度 | `_deg` |
| 角速度 | 度每秒 | `_dps` |
| 线速度 | 米每秒 | `_mps` |
| 时长 | 毫秒 | `_ms` |
| 置信度、比例 | 无量纲 0–1 | 无后缀 |

角度用度不用弧度：网关里的范围校验值是人工写死在代码里的，写 `-170.0` 比写 `-2.9670597` 更不容易看错，评审时也更容易核对。字段名带单位后缀是硬性要求，缺后缀的字段一律视为不合规。

### 2.5 枚举

所有枚举值用大写下划线（`SCREAMING_SNAKE_CASE`），不用数字编码。数字编码在日志里读不出含义，抓包排查时要反查表。全部枚举值集中在附录 B，新增枚举必须同步更新附录。

### 2.6 版本字段

每条报文的顶层必须有 `schema_version`，semver 格式。接收方在解析前检查主版本号，不匹配时丢弃报文并上报一条 `SafetyEvent`（`reason = SCHEMA_VERSION_MISMATCH`），不做兼容性猜测。

---

## 3. IF-1　DetectionEvent　感知事件

`perception` 的唯一输出。巡航态每 100 ms 发一条（哪怕没有检出，也要发空 `detections` 数组，`mission` 靠它判断感知进程还活着）；复核态在二级推理完成后补发一条 `stage = VERIFY` 的报文。

### 3.1 字段表

#### 顶层

| 字段 | 类型 | 必填 | 取值 | 说明 |
|---|---|---|---|---|
| `schema_version` | string | 是 | `"1.0.0"` | semver |
| `msg_type` | string | 是 | `"DETECTION_EVENT"` | 常量 |
| `seq` | uint32 | 是 | | 通道内递增 |
| `ts_mono_ns` | int64 | 是 | | 该帧图像的采集时刻，不是推理完成时刻 |
| `ts_utc_ms` | int64 | 是 | | |
| `run_id` | string | 是 | | |
| `event_id` | string\|null | 是 | UUIDv4 | 巡航态且无可疑目标时为 `null`；一旦某帧判定为可疑，在此生成并沿用到复核结束 |
| `stage` | enum | 是 | `CRUISE` / `VERIFY` | 哪一级产生的 |
| `model` | object | 是 | | 见下 |
| `context` | object | 是 | | 见下 |
| `detections` | array | 是 | 可为空数组 | 见下 |
| `l3_anomaly` | object\|null | 否 | | L3 异常检测结果 |
| `suspect` | object | 是 | | 可疑判定与调度信息 |
| `latency_ms` | object | 是 | | 见下 |

#### `model`

| 字段 | 类型 | 取值 | 说明 |
|---|---|---|---|
| `name` | string | `"yolo11s"` / `"yolo11m"` | |
| `input_w` / `input_h` | uint16 | `640` | 网络输入尺寸 |
| `quant` | enum | `INT8` / `FP16` | |
| `conf_threshold` | float | 巡航 `0.25`，复核 `0.60` | 该帧实际使用的阈值，必须如实填写，答辩要用它对照 Δconf |
| `nms_iou` | float | `0.45` | |

#### `context`

| 字段 | 类型 | 范围 | 说明 |
|---|---|---|---|
| `waypoint_id` | string\|null | | 车当前所在或最近的巡检位 |
| `pose` | object | | `x_m` `y_m` `yaw_deg`，`map` 系；`cov_trace` 位姿协方差迹，用于判断定位是否可信 |
| `pose_valid` | bool | | 定位失锁时为 `false`，此时禁止触发复核 |
| `speed_mps` | float | 0–1.5 | 车当前线速度 |
| `ptz` | object | | `pan_deg` `tilt_deg` `zoom` `hfov_deg`，采集该帧时的云台状态 |
| `image_w` / `image_h` | uint16 | `1920` / `1080` | |

`context.ptz.hfov_deg` 是**当前变焦倍率下**的实际水平视场角，不是广角端的 60°。像素密度公式里的 $\theta$ 取这个值，或者取广角端 $\theta_0$ 与 `zoom` 一起代入，两种算法必须在实现里二选一并注释清楚，不能混用。

#### `detections[]`

| 字段 | 类型 | 范围 | 说明 |
|---|---|---|---|
| `track_id` | int32 | ≥ 0 | 跟踪器分配，跨帧稳定 |
| `defect_class` | enum | 见附录 B.1 | L1 缺陷类别 |
| `confidence` | float | 0–1 | |
| `bbox` | array[4] | `[x1,y1,x2,y2]` px | 左上、右下，`image` 系 |
| `target_size_m` | float | > 0 | 该类别的先验物理尺寸，查表得到，不是测出来的 |
| `est_distance_m` | float | > 0 | 由 bbox 高度与先验尺寸反算，精度有限，只用于像素密度估计 |
| `pixel_density_px` | float | ≥ 0 | 见 3.2 |
| `aim_offset` | object | | `pan_deg` `tilt_deg`，把该目标转到画面中心所需的云台增量 |
| `l2_reading` | object\|null | | 状态量读数，仅 `stage = VERIFY` 且类别属于表计/指示灯时非空 |

#### `l2_reading`

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | enum | `POINTER_GAUGE` / `DIGITAL_DISPLAY` / `INDICATOR_LIGHT` / `SWITCH_POSITION` |
| `value` | float\|string\|null | 指针表和数显给数值，指示灯给 `"RED"` 等字符串，识别失败给 `null` |
| `unit` | string\|null | `"MPa"` / `"A"` / `null` |
| `range_min` / `range_max` | float\|null | 表盘量程，指针表必填 |
| `in_normal_band` | bool\|null | 是否落在正常区间，正常区间由标定阶段配置 |
| `reading_confidence` | float | 0–1 |
| `roi` | array[4] | 读数所用的 ROI，`image` 系 |

#### `l3_anomaly`

| 字段 | 类型 | 说明 |
|---|---|---|
| `model` | string | `"efficientad_s"` |
| `anomaly_score` | float | 归一化到 0–1 |
| `threshold` | float | 当前判异阈值 |
| `is_anomaly` | bool | `anomaly_score > threshold` |
| `heatmap_ref` | string\|null | 证据包内热力图的相对路径，未落盘时为 `null` |

L3 的输出只允许进人工复核队列。任何下游模块不得把 `is_anomaly = true` 当作缺陷判定结果直接上报告警，这是三层缺陷体系的分工约定，写进接口是为了防止实现时图省事把它接到告警通路上。

#### `suspect`

| 字段 | 类型 | 说明 |
|---|---|---|
| `is_suspect` | bool | 是否请求复核 |
| `trigger_rule` | enum\|null | `CONF_BAND` / `L2_UNREADABLE` / `L2_OUT_OF_BAND` / `L3_ANOMALY` / `MANUAL`，见 3.3 |
| `target_track_id` | int32\|null | 请求复核哪个目标 |
| `severity` | float | 0–1，按 `defect_class` 查表，附录 B.1 |
| `novelty` | float | 0–1，本轮首次出现该 `track_id` 取 `1.0`，此前已复核过取 `0.3` |
| `priority` | float | `severity × confidence × novelty` |
| `suppressed_by` | enum\|null | 被抑制时填原因：`TRACK_COOLDOWN` / `WAYPOINT_ONCE` / `RESUME_SILENCE` / `BUDGET_EXHAUSTED` / `POSE_INVALID` |

`is_suspect = true` 且 `suppressed_by = null` 才真正进入复核队列。被抑制的事件仍然要发出来并落日志，否则调参时看不到抑制规则是否过严。

#### `latency_ms`

| 字段 | 说明 |
|---|---|
| `capture_to_infer` | 采集到推理开始 |
| `infer` | 推理耗时 |
| `postproc` | NMS 与后处理 |
| `total` | 采集到本报文发出 |

巡航态 `total` 的 P95 必须小于 100 ms，否则 10 FPS 的节拍保不住。这个字段是联调时唯一的性能观测点，不允许省略。

### 3.2 像素密度的计算约定

$$p = \frac{W \cdot D \cdot z}{2d\tan(\theta/2)}$$

| 符号 | 字段 | 含义 |
|---|---|---|
| $W$ | `context.image_w` | 图像宽度，px |
| $D$ | `detections[].target_size_m` | 目标关键特征的物理尺寸，m |
| $z$ | `context.ptz.zoom` | 光学变焦倍率 |
| $d$ | `detections[].est_distance_m` | 目标距离，m |
| $\theta$ | 广角端水平视场角 60° | 常量，配置在 `camera.yaml` |

标定基准值（写进网关与状态机的判定阈值时以此为准）：

| 场景 | $W$ | $D$ | $z$ | $d$ | $p$ | 结论 |
|---|---|---|---|---|---|---|
| 巡航态遇见指针表 | 1920 | 0.15 | 1 | 5.0 | 49.9 px | 低于可靠读数下限 120 px，必须复核 |
| 复核态 3× 变焦 | 1920 | 0.15 | 3 | 5.0 | 149.6 px | 达标，提升 3.0× |
| 距离上限校核 | 1920 | 0.15 | 3 | 6.24 | 120.0 px | $d_{\max} \approx 6.2$ m |

由此得到两条硬约束，两条都要写进标定规范：

1. 云台光学变焦倍率 $z \geq 3$。$z_{req} = 120 / 49.9 \approx 2.41$，取 3 留余量。
2. 巡检位到表计目标的距离 $d \leq 6$ m。$d_{\max} = 6.24$ m，取 6 留余量。

`pixel_density_px` 由 `perception` 计算并填入报文，`mission` 直接读取，不重复计算。两处各算一遍迟早会因为 $\theta$ 取值不一致而对不上。

### 3.3 复核触发判据

| `trigger_rule` | 条件 | 说明 |
|---|---|---|
| `CONF_BAND` | `0.25 ≤ confidence < 0.60` | 一级检出但不足以定案的置信度带 |
| `L2_UNREADABLE` | 类别属于表计/指示灯 且 `pixel_density_px < 120` | 像素不够，读不出数 |
| `L2_OUT_OF_BAND` | `l2_reading.in_normal_band = false` | 读出来了但超出正常区间，需要更高分辨率确认 |
| `L3_ANOMALY` | `l3_anomaly.is_anomaly = true` | 未知异常 |
| `MANUAL` | 云端下发 | 人工指定复核某巡检位 |

`confidence ≥ 0.60` 的检出直接判定为缺陷，不占复核预算。这是把工作点推向高召回之后仍然能控制复核次数的关键：真正吃预算的只有中间那段置信度带。

### 3.4 JSON Schema

见 `schemas/detection_event.schema.json`，全文附于附录 D.1。

### 3.5 示例报文

巡航态，检出一个疑似压力表读数异常，触发复核请求：

```json
{
  "schema_version": "1.0.0",
  "msg_type": "DETECTION_EVENT",
  "seq": 18422,
  "ts_mono_ns": 884213556000000,
  "ts_utc_ms": 1787462400123,
  "run_id": "20260901-093012-a7f3",
  "event_id": "3f2b9c14-7d5e-4a81-b0c6-2e9f1a4d8e77",
  "stage": "CRUISE",
  "model": {
    "name": "yolo11s", "input_w": 640, "input_h": 640,
    "quant": "INT8", "conf_threshold": 0.25, "nms_iou": 0.45
  },
  "context": {
    "waypoint_id": "WP-07",
    "pose": {"x_m": 12.43, "y_m": -3.18, "yaw_deg": 87.2, "cov_trace": 0.014},
    "pose_valid": true,
    "speed_mps": 0.5,
    "ptz": {"pan_deg": 0.0, "tilt_deg": -2.0, "zoom": 1.0, "hfov_deg": 60.0},
    "image_w": 1920, "image_h": 1080
  },
  "detections": [
    {
      "track_id": 314,
      "defect_class": "PRESSURE_GAUGE",
      "confidence": 0.41,
      "bbox": [812, 431, 869, 488],
      "target_size_m": 0.15,
      "est_distance_m": 5.02,
      "pixel_density_px": 49.7,
      "aim_offset": {"pan_deg": -4.2, "tilt_deg": 1.6},
      "l2_reading": null
    }
  ],
  "l3_anomaly": null,
  "suspect": {
    "is_suspect": true,
    "trigger_rule": "L2_UNREADABLE",
    "target_track_id": 314,
    "severity": 0.7,
    "novelty": 1.0,
    "priority": 0.287,
    "suppressed_by": null
  },
  "latency_ms": {"capture_to_infer": 12, "infer": 38, "postproc": 9, "total": 63}
}
```

---

## 4. IF-2　ControlCommand　控制指令

系统里唯一能让车动、让云台动的通路。安全边界的第一层和第二层都落在这一节。

### 4.1 白名单

协议只承认六条指令。网关收到任何不在下表中的 `command` 值，一律拒绝并上报 `SafetyEvent`，不做任何解释性处理。

| 指令 | 语义 | 参数 | 是否改变车辆运动 |
|---|---|---|---|
| `PAUSE` | 请求停车 | `reason` | 是（减速停止） |
| `RESUME` | 恢复原巡检路线 | 无 | 是（回到路径跟踪） |
| `CREEP_FORWARD` | 沿当前路径小步前移 | `distance_m` | 是（受限位移） |
| `GOTO_OBSERVE` | 前往已标定的观察位 | `waypoint_id`, `tolerance_m` | 是（路径由底盘规划） |
| `PTZ_SET` | 设定云台姿态与变焦 | `pan_deg`, `tilt_deg`, `zoom`, `speed` | 否 |
| `HEARTBEAT` | 心跳 | `mission_state` | 否 |

协议中不存在转向角、轮速、扭矩、制动力、目标速度这类量。AI 侧没有任何字段可以直接指定车怎么动，只能表达"想停"和"想去某个已标定的点"。怎么停、怎么走，由底盘自己决定。

这条约束的实现后果需要在评审时确认：底盘必须提供任务级接口（接受"去 WP-07"这样的指令）。若采购到的底盘只有速度接口，硬件组要在底盘侧写一层适配，把任务级指令翻译成速度指令，这层适配跑在底盘 MCU 上，不在 RK3576 上。这是待拍板事项之一，在 D3 评审前必须有结论，否则本接口无法冻结。

### 4.2 指令报文

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | |
| `msg_type` | string | 是 | `"CONTROL_COMMAND"` |
| `cmd_id` | string | 是 | UUIDv4，ACK 靠它对应 |
| `seq` | uint32 | 是 | |
| `ts_mono_ns` | int64 | 是 | |
| `ts_utc_ms` | int64 | 是 | |
| `run_id` | string | 是 | |
| `event_id` | string\|null | 是 | 复核相关指令必填，心跳为 `null` |
| `issued_by` | enum | 是 | `MISSION_FSM` / `CLOUD_MANUAL` / `WATCHDOG` |
| `command` | enum | 是 | 六选一 |
| `params` | object | 是 | 按 `command` 取不同结构，见 4.3 |
| `timeout_ms` | uint32 | 是 | 期望的完成时限，网关据此判超时 |

### 4.3 各指令参数与网关校验范围

下表右侧两列是**网关进程内硬编码的常量**。网关不从 AI 侧下发的任何配置里读取这些值，也不读 `perception` 或 `mission` 的配置文件。修改这些值需要改网关源码并重新走 D3 级别的评审。

| 指令 | 参数 | 类型 | 网关硬编码范围 | 越界处理 |
|---|---|---|---|---|
| `PAUSE` | `reason` | enum | `VERIFY_REQUEST` / `CLOUD_MANUAL` / `WATCHDOG_RECOVER` | 拒绝 `PARAM_OUT_OF_RANGE` |
| `RESUME` | — | — | — | — |
| `CREEP_FORWARD` | `distance_m` | float | `[0.05, 0.50]` | 拒绝，不做截断 |
| `GOTO_OBSERVE` | `waypoint_id` | string | 必须存在于网关启动时加载的标定表 | 拒绝 `UNKNOWN_WAYPOINT` |
| | `tolerance_m` | float | `[0.10, 0.50]` | 拒绝 |
| `PTZ_SET` | `pan_deg` | float | `[-170.0, 170.0]` | 拒绝 |
| | `tilt_deg` | float | `[-30.0, 60.0]` | 拒绝 |
| | `zoom` | float | `[1.0, 3.0]` | 拒绝 |
| | `speed` | enum | `SLOW` / `NORMAL` | 拒绝 |
| `HEARTBEAT` | `mission_state` | enum | 十个状态之一，见 7.1 | 拒绝 |

越界一律拒绝而不截断。截断会让 AI 侧的 bug 静默通过：发了 5 m 的 `CREEP_FORWARD`，被截成 0.5 m 照常执行，联调时看不出问题，等到某次截断逻辑失效就出事。拒绝会立刻暴露在日志里。

`CREEP_FORWARD` 的 0.5 m 上限是这样定的：它存在的意义是复核时目标被遮挡或距离略超 6.2 m 上限，往前挪一点点。真要移动更远的距离应该用 `GOTO_OBSERVE` 走标定过的路线。0.5 m 是"挪一点"和"走一段"的分界，也是即使指令完全失控、车也只会多走半米的兜底。

### 4.4 ACK 报文

网关对每条指令必须回一条 ACK。REQ/REP 模式下不回 ACK 会阻塞 `mission`，这是有意为之：宁可状态机卡在超时上被日志记下来，也不要指令悄悄丢失。

| 字段 | 类型 | 说明 |
|---|---|---|
| `msg_type` | string | `"COMMAND_ACK"` |
| `cmd_id` | string | 对应的指令 |
| `ts_mono_ns` | int64 | 网关处理完成时刻 |
| `result` | enum | `ACCEPTED` / `REJECTED` / `PREEMPTED` |
| `reject_code` | enum\|null | 见附录 A |
| `reject_detail` | string\|null | 人读的说明，只进日志，不参与逻辑判断 |
| `checks` | object | 逐项校验结果，见下 |
| `exec_handle` | string\|null | `ACCEPTED` 时给出，用于在 `StatusReport` 里追踪该指令的执行进度 |

`checks` 逐项列出网关做了哪些校验：

```json
"checks": {
  "whitelist": "PASS",
  "schema": "PASS",
  "range": "PASS",
  "state_conflict": "PASS",
  "safety_override": "PASS"
}
```

每项取 `PASS` / `FAIL` / `SKIP`。任一项 `FAIL` 则 `result = REJECTED`。这个字段看起来冗余，但它让"网关到底有没有在校验"这件事可观测。评审和验收时抽查日志，如果某一项长期是 `SKIP`，说明那层校验根本没接上。

`ACCEPTED` 只表示指令通过校验并已下发，不表示动作已完成。动作完成与否看 IF-3 的 `StatusReport`。

`PREEMPTED` 表示指令被更高优先级的动作打断，典型情况是执行途中底盘安全层触发。

### 4.5 心跳与看门狗

| 项 | 值 |
|---|---|
| 心跳发送频率 | 5 Hz（200 ms 一条） |
| 网关超时判定 | 1500 ms 内没收到任何 `HEARTBEAT` |
| 超时动作 | 网关自行下发 `RESUME`，`issued_by = WATCHDOG`，并广播 `SafetyEvent(reason = HEARTBEAT_LOST)` |
| 恢复条件 | 心跳恢复且连续 3 条正常，网关解除看门狗态 |

看门狗的动作是让车**继续走完巡检路线**，不是让车停住。理由：AI 进程崩了的时候，车停在配电室通道中间比走完路线回充电位更麻烦。巡检路线本身是标定过的安全路径，底盘沿着它走不需要 AI 参与。真正需要立刻停车的情况由底盘安全层处理，那条通路不经过 AI 也不经过网关。

心跳超时期间，网关拒绝一切 `issued_by = MISSION_FSM` 的指令，`reject_code = HEARTBEAT_LOST`。

### 4.6 示例

复核流程中的一次云台指令与其 ACK：

```json
{
  "schema_version": "1.0.0",
  "msg_type": "CONTROL_COMMAND",
  "cmd_id": "b81e0f42-6c33-4d90-9a15-0f7c2e5b3a88",
  "seq": 2077,
  "ts_mono_ns": 884215901000000,
  "ts_utc_ms": 1787462402468,
  "run_id": "20260901-093012-a7f3",
  "event_id": "3f2b9c14-7d5e-4a81-b0c6-2e9f1a4d8e77",
  "issued_by": "MISSION_FSM",
  "command": "PTZ_SET",
  "params": {"pan_deg": -4.2, "tilt_deg": -0.4, "zoom": 3.0, "speed": "NORMAL"},
  "timeout_ms": 2000
}
```

```json
{
  "schema_version": "1.0.0",
  "msg_type": "COMMAND_ACK",
  "cmd_id": "b81e0f42-6c33-4d90-9a15-0f7c2e5b3a88",
  "ts_mono_ns": 884215903400000,
  "result": "ACCEPTED",
  "reject_code": null,
  "reject_detail": null,
  "checks": {"whitelist":"PASS","schema":"PASS","range":"PASS","state_conflict":"PASS","safety_override":"PASS"},
  "exec_handle": "ptz-0x3f21"
}
```

一条越界指令被拒：

```json
{
  "schema_version": "1.0.0",
  "msg_type": "COMMAND_ACK",
  "cmd_id": "c4a7d011-2e88-4f5b-8c30-9b1e6a2d7f45",
  "ts_mono_ns": 884216104000000,
  "result": "REJECTED",
  "reject_code": "PARAM_OUT_OF_RANGE",
  "reject_detail": "CREEP_FORWARD.distance_m=1.20 exceeds [0.05,0.50]",
  "checks": {"whitelist":"PASS","schema":"PASS","range":"FAIL","state_conflict":"SKIP","safety_override":"SKIP"},
  "exec_handle": null
}
```

---

## 5. IF-3　StatusReport　状态与安全上报

网关向上广播的唯一通道。`mission` 靠它判断指令执行到哪一步，`perception` 靠它拿云台状态填 `DetectionEvent.context.ptz`。

周期上报 20 Hz。安全事件不等周期，立刻插播。

### 5.1 报文结构

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | |
| `msg_type` | string | 是 | `"STATUS_REPORT"` |
| `seq` | uint32 | 是 | |
| `ts_mono_ns` / `ts_utc_ms` | int64 | 是 | |
| `run_id` | string | 是 | |
| `report_kind` | enum | 是 | `PERIODIC` / `SAFETY_EVENT` / `EXEC_UPDATE` |
| `chassis` | object | 是 | |
| `ptz` | object | 是 | |
| `pose` | object | 是 | |
| `watchdog` | object | 是 | |
| `exec` | object\|null | 否 | `report_kind = EXEC_UPDATE` 时必填 |
| `safety` | object\|null | 否 | `report_kind = SAFETY_EVENT` 时必填 |

无论 `report_kind` 是什么，`chassis` / `ptz` / `pose` / `watchdog` 四块都要带完整快照。安全事件插播时也带，这样从任意一条报文都能还原当时的完整状态，排查时不用去拼上一条周期报文。

### 5.2 `chassis`

| 字段 | 类型 | 说明 |
|---|---|---|
| `state` | enum | `MOVING` / `STOPPING` / `STOPPED` / `PAUSED` / `RETURNING` / `FAULT` / `ESTOP` |
| `speed_mps` | float | 当前线速度 |
| `path_progress` | float | 0–1，本轮路线完成比例 |
| `distance_to_goal_m` | float\|null | `GOTO_OBSERVE` 执行中的剩余距离 |
| `current_waypoint_id` | string\|null | |
| `battery_pct` | float | 0–100 |
| `safety_layer_active` | bool | 底盘安全层是否正在介入 |

`STOPPING` 与 `STOPPED` 必须区分。`mission` 的 `HALT_REQ` 状态等的是 `STOPPED`，收到 `STOPPING` 继续等。桩程序模拟 1.5–2.5 s 的停车延迟，就是在模拟这两个状态之间的时间。

`ESTOP` 是急停按钮被按下，与 `FAULT` 不同：`FAULT` 可能自恢复，`ESTOP` 必须人工解除。收到 `ESTOP` 后 `mission` 直接进 `ABORT`，不尝试恢复。

### 5.3 `ptz`

| 字段 | 类型 | 说明 |
|---|---|---|
| `pan_deg` / `tilt_deg` | float | 当前角度，`base_link` 系 |
| `zoom` | float | 当前变焦倍率 |
| `hfov_deg` | float | 当前倍率下的水平视场角 |
| `moving` | bool | 是否在运动中 |
| `focus_state` | enum | `FOCUSING` / `LOCKED` / `FAILED` |
| `at_target` | bool | 是否已到达上一条 `PTZ_SET` 的目标位姿 |

`CAPTURE` 状态必须等到 `at_target = true` 且 `focus_state = LOCKED` 才抓拍。变焦到 3× 之后景深变浅，没对上焦的图送进二级模型只会浪费一次复核预算。

### 5.4 `pose`

| 字段 | 类型 | 说明 |
|---|---|---|
| `x_m` / `y_m` / `yaw_deg` | float | `map` 系 |
| `cov_trace` | float | 位姿协方差迹 |
| `valid` | bool | |
| `source` | enum | `LIDAR_SLAM` / `ODOM_ONLY` / `LOST` |

`source = ODOM_ONLY` 表示定位退化到纯里程计，位置会漂。此时仍然可以巡航，但 `mission` 禁止发起新的复核（`GOTO_OBSERVE` 在漂移的坐标系里没有意义），对应 `DetectionEvent.suspect.suppressed_by = POSE_INVALID`。

### 5.5 `watchdog`

| 字段 | 类型 | 说明 |
|---|---|---|
| `heartbeat_ok` | bool | |
| `last_heartbeat_age_ms` | uint32 | 距上一条心跳的时长 |
| `watchdog_triggered` | bool | 看门狗是否已介入 |

### 5.6 `exec`

| 字段 | 类型 | 说明 |
|---|---|---|
| `exec_handle` | string | 对应 ACK 里给出的句柄 |
| `cmd_id` | string | |
| `progress` | enum | `IN_PROGRESS` / `DONE` / `FAILED` / `PREEMPTED` |
| `elapsed_ms` | uint32 | |
| `fail_reason` | string\|null | |

### 5.7 `safety`

| 字段 | 类型 | 说明 |
|---|---|---|
| `event_type` | enum | 见附录 B.3 |
| `severity` | enum | `INFO` / `WARN` / `CRITICAL` |
| `source` | enum | `CHASSIS_SAFETY_LAYER` / `GATEWAY` / `DRIVER` |
| `action_taken` | enum | `NONE` / `BRAKE` / `ABORT_VERIFY` / `FORCE_RESUME` / `RETURN_HOME` |
| `brake_latency_ms` | uint32\|null | 从事件发生到制动生效的实测时延 |
| `detail` | string | 人读说明 |

### 5.8 安全响应时限

| 事件 | 响应动作 | 时限 | 由谁保证 |
|---|---|---|---|
| 障碍物、碰撞、急停 | 制动 | ≤ 100 ms | 底盘安全层，不经过 AI 与网关 |
| 安全事件发生后中止复核 | 云台归位、状态机进 `ABORT` | ≤ 200 ms | `mission` 收到 `SAFETY_EVENT` 后 |
| 心跳丢失 | 网关下发 `RESUME` | ≤ 1500 ms + 一个指令周期 | `gateway` |

100 ms 这条不由本接口保证。`StatusReport` 里的 `brake_latency_ms` 是底盘报上来的实测值，作用是让这条指标可验收，而不是让 AI 侧去实现它。如果制动依赖 AI 收到报文再下指令，光是 20 Hz 的上报周期就已经吃掉 50 ms，做不到 100 ms。

### 5.9 示例

安全事件插播：

```json
{
  "schema_version": "1.0.0",
  "msg_type": "STATUS_REPORT",
  "seq": 41288,
  "ts_mono_ns": 884219337000000,
  "ts_utc_ms": 1787462405904,
  "run_id": "20260901-093012-a7f3",
  "report_kind": "SAFETY_EVENT",
  "chassis": {
    "state": "STOPPING", "speed_mps": 0.18, "path_progress": 0.34,
    "distance_to_goal_m": null, "current_waypoint_id": "WP-07",
    "battery_pct": 71.5, "safety_layer_active": true
  },
  "ptz": {
    "pan_deg": -4.2, "tilt_deg": -0.4, "zoom": 3.0, "hfov_deg": 20.4,
    "moving": false, "focus_state": "LOCKED", "at_target": true
  },
  "pose": {"x_m": 12.61, "y_m": -3.20, "yaw_deg": 87.4, "cov_trace": 0.016, "valid": true, "source": "LIDAR_SLAM"},
  "watchdog": {"heartbeat_ok": true, "last_heartbeat_age_ms": 143, "watchdog_triggered": false},
  "exec": null,
  "safety": {
    "event_type": "OBSTACLE_DETECTED",
    "severity": "CRITICAL",
    "source": "CHASSIS_SAFETY_LAYER",
    "action_taken": "BRAKE",
    "brake_latency_ms": 68,
    "detail": "front lidar sector 3, range 0.42m"
  }
}
```

---

## 6. IF-4　EvidencePackage　证据包

一次复核的完整产物。它同时是三件东西：给云端的告警载荷、答辩时的证据、算法迭代的数据来源。字段设计以第三点为主，因为前两点用到的字段是第三点的子集。

### 6.1 目录结构

证据包在边缘先落盘成一个目录，上传时元数据走 MQTT、文件走 HTTPS。

```
evidence/<run_id>/<event_id>/
├── manifest.json          IF-4 报文本体
├── cruise.jpg             一级检出的原始帧（1920×1080，含检出框）
├── cruise_raw.jpg         同一帧无标注原图，用于重训练
├── verify_01.jpg          主视角复核抓拍第 1 帧
├── verify_02.jpg          第 2 帧
├── verify_03.jpg          第 3 帧
├── verify_aux_l.jpg       A3 条件式辅视角，左偏 15°（仅条件路径）
├── verify_aux_r.jpg       右偏 15°（仅条件路径）
├── verify_roi.jpg         L2 读数所用 ROI 裁图
├── anomaly_heat.png       L3 热力图，无 L3 时缺省
└── meta.jsonl             复核期间全部 StatusReport 与 ACK 的原始流水
```

抓三帧而不是一帧：云台停稳后仍有残余抖动，3 帧里挑最清晰的一帧送二级模型，成本是 0.6 s，收益是显著降低运动模糊导致的复核失败。三帧全部入包，因为丢弃的两帧对分析复核失败原因有用。

**辅视角两张只在条件路径上出现**（A3），`role` 为 `VERIFY_FRAME_AUX`，与主视角的 `VERIFY_FRAME` 分开——它们解决的是两个不同问题：连拍抗运动模糊，辅视角抗镜面高光。合成一个角色会让「这次复核为什么慢了 1.5 s」无从查起。

`meta.jsonl` 是本次复核的完整回放数据。有了它，一次线上复核失败可以在桩环境里逐帧重放，不用去现场复现。单次约 200 KB，不构成负担。

### 6.2 `manifest.json` 字段表

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | |
| `msg_type` | string | `"EVIDENCE_PACKAGE"` |
| `run_id` / `event_id` | string | |
| `waypoint_id` | string | |
| `ts_utc_ms` | int64 | 复核开始时刻 |
| `verdict` | object | 复核结论，见 6.3 |
| `before` | object | 一级检出快照 |
| `after` | object | 二级复核快照 |
| `gain` | object | 复核增益指标，见 6.4 |
| `timeline` | array | 状态机各状态的实际耗时 |
| `files` | array | 文件清单，见 6.5 |
| `abort` | object\|null | 复核未完成时的中止信息 |

`before` 与 `after` 各自是一个精简的检测结果：`confidence` / `pixel_density_px` / `zoom` / `est_distance_m` / `defect_class` / `l2_reading`。两者结构相同，方便直接做差。

### 6.3 `verdict`

| 字段 | 类型 | 说明 |
|---|---|---|
| `result` | enum | `CONFIRMED_DEFECT` / `FALSE_ALARM` / `READING_OK` / `READING_ABNORMAL` / `UNKNOWN_ANOMALY` / `INCONCLUSIVE` |
| `defect_class` | string\|null | 确认后的类别 |
| `severity` | enum | `INFO` / `WARN` / `CRITICAL` |
| `needs_human_review` | bool | `UNKNOWN_ANOMALY` 与 `INCONCLUSIVE` 恒为 `true` |
| `confidence` | float | 二级模型置信度 |

`FALSE_ALARM` 是有价值的结论，不是失败。一级为了保召回把阈值压到 0.25，必然带来误报，复核把它们消解掉正是这套方案的立论所在。误报被复核否掉并记录下来，这条数据回流到训练集，下一轮一级模型在这类背景上的误报率就会下降。

### 6.4 `gain`　复核增益指标

答辩的三项关键指标全部落在这里。

| 字段 | 类型 | 计算 | 目标 |
|---|---|---|---|
| `delta_conf` | float | `after.confidence − before.confidence` | > +0.25 |
| `pixel_density_ratio` | float | `after.pixel_density_px / before.pixel_density_px` | > 2.2 |
| `verify_success` | bool | `verdict.result ∉ {INCONCLUSIVE}` 且未中止 | 成功率 > 85% |

三项由 `uploader` 在打包时计算并写死在 manifest 里，不由云端二次计算。云端重算会引入版本不一致的风险，而这三个数字是要写进答辩材料的。

`delta_conf` 对 `FALSE_ALARM` 会是负值，这是正常的：复核把一个 0.41 的误检压到 0.05，`delta_conf = −0.36`。统计时按 `verdict.result` 分组，`CONFIRMED_DEFECT` 组的均值才是立论要证明的那个数。这一点在做统计脚本时最容易搞错，把两组混在一起算会让均值接近零，看上去像是复核没起作用。

### 6.5 `files`

| 字段 | 类型 | 说明 |
|---|---|---|
| `path` | string | 包内相对路径 |
| `role` | enum | `CRUISE_ANNOTATED` / `CRUISE_RAW` / `VERIFY_FRAME` / `VERIFY_ROI` / `ANOMALY_HEATMAP` / `META_LOG` |
| `bytes` | uint32 | |
| `sha256` | string | 断点续传与完整性校验 |
| `uploaded` | bool | 边缘侧维护的上传状态 |

### 6.6 上传协议

| 环节 | 方式 | 说明 |
|---|---|---|
| 元数据 | MQTT topic `patrol/<site_id>/<run_id>/evidence`，QoS 1 | `manifest.json` 全文，单条 < 16 KB |
| 文件 | HTTPS PUT，URL 由云端在 MQTT 响应中签发 | 逐文件上传，按 `files[].sha256` 去重 |
| 断网 | 边缘落盘保留，`uploaded = false` | 网络恢复后按 `ts_utc_ms` 由旧到新补传 |
| 保留策略 | 本地磁盘保留最近 7 天或 20 GB，先到先删；已上传的优先删 | |
| 重传上限 | 单文件 5 次，仍失败则标记 `UPLOAD_FAILED` 并保留本地 | |

先传元数据再传文件：断网恢复后即使文件还没传完，云端已经知道发生过什么、结论是什么。告警的时效性由元数据保证，图片是事后佐证。

### 6.7 示例

```json
{
  "schema_version": "1.0.0",
  "msg_type": "EVIDENCE_PACKAGE",
  "run_id": "20260901-093012-a7f3",
  "event_id": "3f2b9c14-7d5e-4a81-b0c6-2e9f1a4d8e77",
  "waypoint_id": "WP-07",
  "ts_utc_ms": 1787462400123,
  "verdict": {
    "result": "READING_ABNORMAL",
    "defect_class": "PRESSURE_GAUGE",
    "severity": "WARN",
    "needs_human_review": false,
    "confidence": 0.91
  },
  "before": {
    "confidence": 0.41, "pixel_density_px": 49.7, "zoom": 1.0,
    "est_distance_m": 5.02, "defect_class": "PRESSURE_GAUGE", "l2_reading": null
  },
  "after": {
    "confidence": 0.91, "pixel_density_px": 149.1, "zoom": 3.0,
    "est_distance_m": 5.02, "defect_class": "PRESSURE_GAUGE",
    "l2_reading": {
      "kind": "POINTER_GAUGE", "value": 0.42, "unit": "MPa",
      "range_min": 0.0, "range_max": 1.6, "in_normal_band": false,
      "reading_confidence": 0.88, "roi": [640, 300, 1280, 780]
    }
  },
  "gain": {"delta_conf": 0.50, "pixel_density_ratio": 3.00, "verify_success": true},
  "timeline": [
    {"state": "SUSPECT",  "duration_ms": 180},
    {"state": "HALT_REQ", "duration_ms": 1960},
    {"state": "AIM",      "duration_ms": 1440},
    {"state": "ZOOM",     "duration_ms": 1210},
    {"state": "CAPTURE",  "duration_ms": 590},
    {"state": "VERIFY",   "duration_ms": 2380},
    {"state": "PACK",     "duration_ms": 470},
    {"state": "RESUME",   "duration_ms": 290}
  ],
  "files": [
    {"path": "cruise.jpg", "role": "CRUISE_ANNOTATED", "bytes": 284419, "sha256": "9f1c...", "uploaded": true},
    {"path": "verify_02.jpg", "role": "VERIFY_FRAME", "bytes": 311902, "sha256": "3ab7...", "uploaded": true},
    {"path": "verify_roi.jpg", "role": "VERIFY_ROI", "bytes": 61233, "sha256": "c40e...", "uploaded": true},
    {"path": "meta.jsonl", "role": "META_LOG", "bytes": 198744, "sha256": "77d2...", "uploaded": false}
  ],
  "abort": null
}
```

---

## 7. 状态机与接口的对应关系

本节把十状态复核状态机翻译成接口调用序列。它不是状态机的设计文档，而是接口的使用说明：每个状态发哪条指令、等哪个字段、超时多少。写接口文档时把这一节放进来，是因为"什么时候可以发 `CREEP_FORWARD`"这类问题只看字段表答不出来。

### 7.1 状态枚举

`HEARTBEAT.params.mission_state` 的取值就是这十个：

`CRUISE` / `SUSPECT` / `HALT_REQ` / `AIM` / `ZOOM` / `CAPTURE` / `VERIFY` / `PACK` / `RESUME` / `ABORT`

### 7.2 各状态的接口行为与时序预算

| 状态 | 发出 | 等待条件 | 预算 | 超时 | 超时动作 |
|---|---|---|---|---|---|
| `CRUISE` | 仅 `HEARTBEAT` | `DetectionEvent.suspect.is_suspect = true` | — | — | — |
| `SUSPECT` | 无 | 连续三帧确认 + 三重抑制与预算检查通过 | 0.3 s | 0.5 s | 回 `CRUISE` |
| `HALT_REQ` | `PAUSE(VERIFY_REQUEST)` | `chassis.state = STOPPED` | 2.0 s | 4.0 s | `ABORT` |
| `AIM` | `PTZ_SET(pan,tilt,zoom=1)` | `ptz.at_target = true` | 1.5 s | 3.0 s | `ABORT` |
| `ZOOM` | `PTZ_SET(pan,tilt,zoom=z_cmd)` | `at_target` 且 `focus_state = LOCKED` | 1.5 s | 2.5 s | `ABORT` |
| `CAPTURE` | 无（走 `ICamera`；条件路径另发 `PTZ_SET` 偏转） | 主视角 3 帧完成；判定需辅视角时，三视角各 3 帧完成 | 0.6 s（条件路径 2.1 s） | 4.0 s | `ABORT` |
| `VERIFY` | 无（走 `perception`） | 收到 `stage = VERIFY` 的 `DetectionEvent` | 2.5 s | 5.0 s | `ABORT` |
| `PACK` | 无（走 `uploader`） | manifest 落盘完成 | 0.5 s | 2.0 s | 记 `PACK_FAILED`，仍转 `RESUME` |
| `RESUME` | `PTZ_SET(0,0,1)` + `RESUME` | `chassis.state = MOVING` | 0.3 s | 1.0 s | 重发一次，仍失败则 `ABORT` |
| `ABORT` | `PTZ_SET(0,0,1)` + `RESUME` | `chassis.state = MOVING` | — | 1.0 s | 上报 `RESUME_FAILED`，由看门狗兜底 |

$$T_r = 0.3 + 2.0 + 1.5 + 1.5 + 0.6 + 2.5 + 0.5 + 0.3 = 9.2\ \text{s}$$

每个状态都有独立超时且超时动作都指向 `ABORT` 或 `CRUISE`，状态图里不存在没有出边的节点，也不存在只能靠外部干预才能离开的状态。

`AIM` 与 `ZOOM` 拆成两条 `PTZ_SET` 而不是一条：先在广角端把目标转到画面中心，再变焦。反过来做的话，变焦后视场只有 20° 左右，转向时目标很容易划出画面，重新找回来的代价远大于多发一条指令。

**`ZOOM` 下发的是按需算出的 `z_cmd`，不是固定的 3×**（D3 决议 C4，采纳方案书 §6.3.5）：

$$z\_cmd = \mathrm{clip}\left(z_{cur} \cdot \frac{p_{target}}{p_{cur}},\ 1,\ 3\right)$$

固定 3× 对近距离目标会过度放大导致目标出框——方案书 §9.4 的问题预案里「变焦后目标丢失」写的就是这个。校验不过时重试一次，因此预算由 1.2 s 放宽到 1.5 s。

**`SUSPECT` 需要连续三帧确认**（D3 决议 C10，取方案书 §6.4 的三帧口径）。原预算 0.2 s 是按 10 fps 两帧算的，三帧需要 0.3 s。

**`CAPTURE` 的三视角是条件式的，不是无条件的**（D3 决议 A3）。默认走单视角连拍 3 帧（0.6 s）；当质量评价判定主视角存在高光遮挡（`detections[].quality.highlight_ratio`）或首次读数置信度低于阈值时，追加左右各 ±15° 两个辅视角，`CAPTURE` 延长到 2.1 s，并做三视角一致性判定（读数极差写进 `after.multiview_spread`，超过 0.5 % FS 判本次测量不可信）。

方案书 §4.3.1 把表盘玻璃的镜面反射列为本场景最主要的光学干扰源，抑制手段正是「改变云台角度重新拍摄」——这条不能删；但它按定义就是条件触发的，无条件付 2.1 s 会让 $N_{\max}$ 从 21 掉到 18，用 14 % 的复核能力去换一个只在高光时用得上的手段。

**超时因此由 1.5 s 放宽到 4.0 s**（连锁 C1）：条件路径要 2.1 s，1.5 s 会让走辅视角的复核必然超时进 `ABORT`。

统计 `CAPTURE` 耗时时要按是否走辅视角分组，`timeline` 里它是双峰分布，直接取均值没有意义。

`ABORT` 的出口动作和 `RESUME` 完全一致。区别只在于是否产出证据包：`ABORT` 时 `manifest.abort` 非空，记录中止在哪个状态、原因是什么，`gain.verify_success = false`。中止的复核照样打包上传，因为复核失败的样本对调参最有价值。

### 7.3 三重抑制

在 `SUSPECT` 状态检查，任一条命中则不进入复核，回 `CRUISE`，并在 `DetectionEvent.suspect.suppressed_by` 里注明。

| 规则 | 参数 | 键 | 说明 |
|---|---|---|---|
| 同目标冷却 | 60 s | `track_id` | 同一个 `track_id` 复核过后 60 s 内不再复核 |
| 同巡检位单次 | 2 m 半径 | 复核发生时的 `pose` | 本轮内，以该点为心 2 m 内只复核一次 |
| 恢复静默 | 3 s | 全局 | `RESUME` 之后 3 s 内不接受任何新的复核请求 |

三条规则针对的是三种不同的死循环。冷却挡的是同一个目标反复触发；巡检位单次挡的是跟踪器丢了 ID 之后同一个目标以新 `track_id` 再次触发；恢复静默挡的是车刚起步、云台刚归位那一瞬间画面剧烈变化引发的连锁触发。少任何一条都有一类循环补不上。

冷却与巡检位记录随 `run_id` 清空，不跨轮保留。

### 7.4 复核预算

$$N_{\max} = \left\lfloor \frac{T_{\max} - L/v}{T_r} \right\rfloor$$

标定算例：$L = 200$ m，$v = 0.5$ m/s，$T_{\max} = 600$ s，$T_r = 9.2$ s

$$N_{\max} = \left\lfloor \frac{600 - 400}{9.2} \right\rfloor = \lfloor 21.7 \rfloor = 21$$

> **这是算例，不是本车的实际配置。** 读了底盘固件之后才知道实车最高 0.3 m/s
> （`MAX_SPD 0.1` × 三档，见 `docs/底盘固件评审.md` §6），200 m 路线光巡航就要
> 667 s，超过 600 s 上限，$N_{\max} = 0$。`configs/system.yaml` 因此按
> 60 m / 0.25 m/s 标定，$N_{\max} = 39$。`validate.py` 第 4 项同时校验算例与
> 当前配置两套数，后者要求 $N_{\max} > 0$。

预算耗尽后，`suspect.is_suspect` 仍然照常置位，但 `suppressed_by = BUDGET_EXHAUSTED`，事件进入顺延队列，按 `priority` 排序，下一轮巡检优先处理。

$$priority = severity \times confidence \times novelty$$

`novelty` 取 0.3 而不是 0，是为了让复现的缺陷仍有机会被复核，只是排在新发现之后。取 0 会导致某个缺陷第一轮复核失败之后永远排不上队。

`mission` 在 `HEARTBEAT` 里不带预算余量，预算是 `mission` 的内部状态。网关不参与预算判断，这是有意的分层：网关只管单条指令的合法性，不管任务层的调度。

---

## 8. 驱动层抽象接口

网关之下、硬件之上的一层。它存在的唯一理由是让桩和真机可以互换：`gateway` 只依赖这四个抽象基类，启动时按配置注入桩实现或真机实现，网关代码一行不改。

文件：`patrol/drivers/base.py`

### 8.1 三条约定

**一、所有会改变物理状态的方法都是非阻塞的。** 它们立即返回一个 `ExecHandle`，调用方通过 `poll(handle)` 查询进度。阻塞式的 `goto_and_wait()` 看起来省事，但状态机的每状态超时会失效——超时逻辑在状态机里，阻塞在驱动里，两者管不到对方。

**二、驱动层不承担任务级安全校验。** `distance_m ≤ 0.5` 这类约束在网关。驱动层只按硬件能力校验（云台转不到 200°），越界抛 `ParamOutOfRange`，不截断。两层校验的依据不同：网关的依据是任务需求，驱动的依据是硬件手册。

**三、`capabilities()` 是开机自检的依据。** 系统启动时 `mission` 读取各驱动的能力声明，与任务需求对照，不满足则拒绝启动并打印差在哪。这条把像素密度判据从纸面结论变成了运行时检查：`PTZCaps.max_zoom < 3.0` 时系统直接不启动，而不是等到现场发现表读不出来。

### 8.2 公共类型

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional
import numpy as np


class ExecProgress(Enum):
    IN_PROGRESS = "IN_PROGRESS"
    DONE        = "DONE"
    FAILED      = "FAILED"
    PREEMPTED   = "PREEMPTED"


@dataclass(frozen=True)
class ExecHandle:
    """一次异步动作的句柄。handle_id 会原样出现在 COMMAND_ACK.exec_handle。"""
    handle_id: str
    issued_ts_mono_ns: int


@dataclass
class ExecResult:
    progress: ExecProgress
    elapsed_ms: int
    fail_reason: Optional[str] = None


# ---- 异常 ----
class DriverError(Exception):
    """驱动层异常基类。网关捕获后转成 COMMAND_ACK 的 reject_code。"""

class DriverNotReady(DriverError):
    """硬件未初始化或已断开。"""

class ParamOutOfRange(DriverError):
    """参数超出硬件能力，不截断，直接抛。"""

class DriverTimeout(DriverError):
    """底层通信超时。与状态机的状态超时是两回事。"""
```

### 8.3 `IChassis`　底盘

```python
class ChassisState(Enum):
    MOVING    = "MOVING"
    STOPPING  = "STOPPING"
    STOPPED   = "STOPPED"
    PAUSED    = "PAUSED"
    RETURNING = "RETURNING"
    FAULT     = "FAULT"
    ESTOP     = "ESTOP"


@dataclass
class ChassisStatus:
    state: ChassisState
    speed_mps: float
    path_progress: float                    # 0-1
    distance_to_goal_m: Optional[float]
    current_waypoint_id: Optional[str]
    battery_pct: float
    safety_layer_active: bool
    ts_mono_ns: int


@dataclass
class ChassisCaps:
    supports_task_level: bool               # 是否支持 GOTO_OBSERVE 这类任务级指令
    max_speed_mps: float
    max_creep_m: float                      # 硬件允许的单次微动上限
    has_safety_layer: bool                  # 是否具备独立于上层的安全层
    waypoint_ids: list[str]                 # 底盘侧已加载的巡检位


class IChassis(ABC):
    """底盘驱动。实现方：硬件组（真机）/ 软件组（chassis_stub）。

    supports_task_level 为 False 时，mission 拒绝启动。速度级接口无法
    在不引入转向控制的前提下实现 GOTO_OBSERVE，而转向控制不在协议白名单内。
    """

    @abstractmethod
    def capabilities(self) -> ChassisCaps: ...

    @abstractmethod
    def pause(self, reason: str) -> ExecHandle:
        """请求停车。完成判据是 status().state == STOPPED，不是本函数返回。"""

    @abstractmethod
    def resume(self) -> ExecHandle:
        """恢复原巡检路线。从任何非 ESTOP 状态调用都必须被接受。"""

    @abstractmethod
    def creep_forward(self, distance_m: float) -> ExecHandle:
        """沿当前路径前移。distance_m > caps.max_creep_m 时抛 ParamOutOfRange。"""

    @abstractmethod
    def goto_observe(self, waypoint_id: str, tolerance_m: float) -> ExecHandle:
        """前往已标定观察位。waypoint_id 不在 caps.waypoint_ids 中时抛 ParamOutOfRange。"""

    @abstractmethod
    def status(self) -> ChassisStatus: ...

    @abstractmethod
    def poll(self, handle: ExecHandle) -> ExecResult: ...

    @abstractmethod
    def subscribe_safety(self, cb: Callable[[dict], None]) -> None:
        """注册安全事件回调。回调在驱动内部线程触发，实现方必须保证
        从事件发生到回调被调用不超过 20 ms。回调内不得阻塞。"""

    @abstractmethod
    def close(self) -> None: ...
```

`resume()` 必须从任何非 `ESTOP` 状态都能被接受，这条是给看门狗用的。AI 进程崩溃时网关下发 `RESUME`，此时底盘可能处于 `PAUSED`、`STOPPING`、`FAULT` 中的任意一个，如果驱动因为状态不对而拒绝，车就真的卡在路上了。

### 8.4 `IPTZ`　云台

```python
class PTZSpeed(Enum):
    SLOW   = "SLOW"
    NORMAL = "NORMAL"


class FocusState(Enum):
    FOCUSING = "FOCUSING"
    LOCKED   = "LOCKED"
    FAILED   = "FAILED"


@dataclass
class PTZStatus:
    pan_deg: float
    tilt_deg: float
    zoom: float
    hfov_deg: float                         # 当前倍率下的实际水平视场角
    moving: bool
    focus_state: FocusState
    at_target: bool
    ts_mono_ns: int


@dataclass
class PTZCaps:
    pan_range_deg: tuple[float, float]
    tilt_range_deg: tuple[float, float]
    max_zoom: float                         # 光学变焦，不含数字变焦
    hfov_at_1x_deg: float                   # 广角端水平视场角，像素密度公式的 θ
    zoom_is_optical: bool


class IPTZ(ABC):
    """云台与变焦驱动。

    max_zoom < 3.0 或 zoom_is_optical 为 False 时，mission 拒绝启动：
    数字变焦不增加感光像素，p 公式对它不成立。
    """

    @abstractmethod
    def capabilities(self) -> PTZCaps: ...

    @abstractmethod
    def set_pose(self, pan_deg: float, tilt_deg: float,
                 zoom: float, speed: PTZSpeed) -> ExecHandle:
        """设定目标位姿。任一参数超出 caps 范围时抛 ParamOutOfRange。"""

    @abstractmethod
    def home(self) -> ExecHandle:
        """归位到 (0, 0, 1.0)。ABORT 与 RESUME 都调用它。"""

    @abstractmethod
    def status(self) -> PTZStatus: ...

    @abstractmethod
    def poll(self, handle: ExecHandle) -> ExecResult: ...

    @abstractmethod
    def close(self) -> None: ...
```

`hfov_at_1x_deg` 由驱动声明而不是写在配置文件里，是为了让桩和真机各自报告自己的真实值。`ptz_stub` 用 4K 视频裁剪仿真，它的等效视场角与真机镜头不同，写死在配置里会导致换硬件时忘了改。

### 8.5 `ICamera`　相机

```python
@dataclass
class Frame:
    seq: int
    ts_mono_ns: int                         # 曝光开始时刻，不是取回时刻
    ts_utc_ms: int
    image: np.ndarray                       # HxWx3, BGR, uint8
    width: int
    height: int


@dataclass
class CameraCaps:
    width: int
    height: int
    max_fps: int
    pixel_format: str                       # "BGR888"


class ICamera(ABC):
    """相机驱动。真机走 RK3576 的 MPP 硬解，桩走视频文件解码。"""

    @abstractmethod
    def capabilities(self) -> CameraCaps: ...

    @abstractmethod
    def start(self, width: int, height: int, fps: int) -> None: ...

    @abstractmethod
    def grab(self, timeout_ms: int = 200) -> Frame:
        """取一帧最新图像。超时抛 DriverTimeout。"""

    @abstractmethod
    def grab_burst(self, n: int, interval_ms: int) -> list[Frame]:
        """连拍 n 帧。CAPTURE 状态用 n=3, interval_ms=150。
        必须保证 n 帧之间没有丢帧，不足 n 帧抛 DriverError。"""

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...
```

`Frame.ts_mono_ns` 取曝光开始时刻。取回时刻会把解码延迟算进去，而 `DetectionEvent.latency_ms.capture_to_infer` 要观测的正是这段延迟，用取回时刻会让它恒等于零，观测点就废了。

### 8.6 `ILocalizer`　定位

```python
class PoseSource(Enum):
    LIDAR_SLAM = "LIDAR_SLAM"
    ODOM_ONLY  = "ODOM_ONLY"
    LOST       = "LOST"


@dataclass
class Pose:
    x_m: float
    y_m: float
    yaw_deg: float
    cov_trace: float
    valid: bool
    source: PoseSource
    ts_mono_ns: int


class ILocalizer(ABC):
    @abstractmethod
    def get_pose(self) -> Pose: ...

    @abstractmethod
    def subscribe(self, cb: Callable[[Pose], None]) -> None:
        """位姿更新回调，频率不低于 10 Hz。"""

    @abstractmethod
    def close(self) -> None: ...
```

### 8.7 注入方式

```python
# patrol/drivers/factory.py
def build_drivers(cfg: dict) -> tuple[IChassis, IPTZ, ICamera, ILocalizer]:
    mode = cfg["driver_mode"]        # "stub" | "real"
    ...
```

`driver_mode` 是配置文件里唯一区分桩和真机的开关。除了 `factory.py`，任何其他文件出现 `if mode == "stub"` 都算违反本约定，评审时会检查。桩和真机的差异必须全部封在实现类里，一旦泄漏到业务代码，"桩环境验证过的逻辑在真机上同样成立"这个前提就不成立了，而整个无硬件并行开发方案就是建立在这个前提上的。

---

## 9. 桩的行为契约

三个桩实现第 8 节的抽象基类。它们不是"能跑就行"的假实现：桩要主动制造真机上会出现的麻烦，否则在桩上调通的状态机一上真机就崩。

桩的行为由 `stub.yaml` 配置，联调时通过改配置切换故障场景，不改代码。

### 9.1 `chassis_stub`

| 注入项 | 配置键 | 默认值 | 模拟的真实情况 |
|---|---|---|---|
| 停车延迟 | `stop_delay_ms` | 均匀分布 `[1500, 2500]` | 减速到停稳需要时间，且不确定 |
| ACK 丢失 | `ack_drop_rate` | `0.02` | 串口/CAN 偶发丢包 |
| 安全事件注入 | `safety_event_rate` | `0.05` 次每分钟 | 行人、临时堆放物 |
| 安全事件类型分布 | `safety_event_types` | `OBSTACLE_DETECTED` 0.8 / `BUMPER_HIT` 0.15 / `ESTOP_PRESSED` 0.05 | |
| 制动时延 | `brake_latency_ms` | 均匀分布 `[40, 95]` | 底盘安全层的实际响应 |
| `GOTO_OBSERVE` 到位误差 | `goto_error_m` | 正态 `σ=0.08` | 路径跟踪精度 |
| 电量 | `battery_drain_pct_per_min` | `0.4` | |

`stop_delay_ms` 的下限 1500 ms 与 `HALT_REQ` 的 2000 ms 预算之间只有 500 ms 余量，上限 2500 ms 已经超出预算。这是故意的：预算是均值意义上的，超出预算但不超出 4000 ms 超时的情况必须在桩上出现，否则没人会去测"复核偶尔慢一点会怎样"。

`ack_drop_rate = 0.02` 意味着大约每 50 条指令丢一条。一轮巡检 22 次复核、每次 4 条指令，将近 90 条，一轮里大概率会丢一条。状态机必须能扛住，扛不住就会在 M2 的"连续 3 次触发"上翻车。

`ESTOP_PRESSED` 保留 5% 的概率，因为它是唯一不能自恢复的安全事件。这条路径如果不在桩上跑过，真机上第一次按急停就是现场事故。

### 9.2 `ptz_stub`

用一段 4K（3840×2160）实拍视频做 ROI 裁剪缩放，仿真云台的转动与变焦。

裁剪规则：给定 $(pan, tilt, zoom)$，在 4K 帧上取一块 $\frac{3840}{z} \times \frac{2160}{z}$ 的 ROI，中心由 $(pan, tilt)$ 换算得到，再缩放到 1920×1080 输出。

| $z$ | ROI 尺寸 | 与 1920 输出的关系 | 有效感光像素比 |
|---|---|---|---|
| 1× | 3840×2160 | 降采样 2:1 | 1.00（过采样，截断为 1.0） |
| 2× | 1920×1080 | 一比一 | 1.00 |
| 3× | 1280×720 | 上采样 1.5:1 | 0.67 |

有效像素比 $k = \min\left(1,\ \frac{2}{z}\right)$。桩上的等效像素密度是

$$p_{stub} = p \cdot k = \frac{W \cdot D \cdot z}{2d\tan(\theta/2)} \cdot \min\left(1, \frac{2}{z}\right)$$

**这条差异必须写进 M2 的验收条件。** 3× 时桩只有真机 2/3 的信息量，按 120 px 的读数下限反推：

| 环境 | $z$ | 达到 120 px 等效所需距离 |
|---|---|---|
| 真机 3× 光变 | 3 | $d \leq 6.24$ m |
| `ptz_stub` | 3 | $d \leq 4.16$ m |

桩环境的标定素材要把表计目标布在 4 m 以内。如果照着真机的 6 m 布素材，L2 读数会在桩上大面积失败，而失败原因是仿真损失不是算法问题，排查会浪费掉整块时间。反过来，桩上能读出来的表，真机上一定能读出来，桩是一个偏保守的下界，这个方向的偏差是安全的。

其余注入项：

| 注入项 | 配置键 | 默认值 | 说明 |
|---|---|---|---|
| 转动速度 | `pan_speed_dps` / `tilt_speed_dps` | `60` / `40` | 决定 `AIM` 耗时 |
| 变焦耗时 | `zoom_time_ms` | `[800, 1400]` | 决定 `ZOOM` 耗时 |
| 对焦失败率 | `focus_fail_rate` | `0.05` | 触发 `focus_state = FAILED` |
| 到位抖动 | `settle_jitter_deg` | `0.15` | `at_target` 置位后仍有残余抖动，验证连拍 3 帧的必要性 |
| 声明的 `max_zoom` | `max_zoom` | `3.0` | 改成 `2.0` 可验证开机自检是否真的会拒绝启动 |

### 9.3 `pose_stub`

播放一段预录的位姿序列，或按标定路线程序生成。

| 注入项 | 配置键 | 默认值 | 说明 |
|---|---|---|---|
| 位姿频率 | `rate_hz` | `20` | |
| 高斯噪声 | `noise_sigma_m` | `0.02` | |
| 定位失锁 | `lost_rate` | `0.02` 次每分钟 | 进入 `source = ODOM_ONLY` |
| 失锁时长 | `lost_duration_ms` | `[3000, 12000]` | |
| 失锁期漂移 | `drift_mps` | `0.05` | 纯里程计的累积误差 |
| 序列文件 | `pose_file` | `routes/wp_loop_200m.jsonl` | 200 m 标定路线 |

失锁必须注入，因为 `POSE_INVALID` 抑制规则的正确性只能靠它验证。失锁期间状态机应当继续巡航但不发起复核，失锁恢复后被压下的事件按 `priority` 排队重试。这条逻辑没有失锁注入就是死代码。

### 9.4 桩与真机的一致性要求

桩实现与真机实现必须在这几点上完全一致，不一致会让桩上的结论失效：

1. 抽象基类的方法签名、返回类型、异常类型
2. 非阻塞语义：`set_pose()` 立即返回，`at_target` 由后续 `status()` 反映
3. `capabilities()` 如实声明，不允许桩为了"跑通"而虚报能力
4. 时间戳语义：`Frame.ts_mono_ns` 是曝光时刻

允许不一致的只有一处：数值上的性能差异（延迟、噪声、有效像素比）。这些差异必须在本节量化列出，量化过的差异可以在验收条件里补偿，没量化的差异只会变成排查不出来的怪问题。

---

## 10. 一致性校验与 M1 评审

### 10.1 校验脚本

仓库里附 `validate.py`。它做七件事：

1. 五份 Schema 自身是否是合法的 Draft 2020-12
2. 抽取本文档所有 `json` 代码块，按 `msg_type` 找到对应 Schema 并校验
3. 复核像素密度算例（49.9 / 149.6 / 120.0 px，$z_{req}$，$d_{\max}$，桩的 $d_{\max}$）
4. 复核时序预算加总是否等于 9.2 s，$N_{\max}$ 是否等于 21
5. 检查每个状态的超时是否都大于其预算
6. 比对附录 D 内嵌的 Schema 与 `schemas/` 下的文件是否逐字节一致，防止文档与代码各改各的
7. 跑九条反例，确认越界指令、协议外参数、自相矛盾的字段组合都被 Schema 拦下

第 7 项是重点。Schema 写出来容易，写出来之后约束是否真的生效是另一回事。九条反例覆盖的是最不该漏的几类：

| 反例 | 拦住它的机制 |
|---|---|
| `CREEP_FORWARD.distance_m = 1.20` | `maximum: 0.50` |
| `PTZ_SET.zoom = 5.0` | `maximum: 3.0` |
| `command = "SET_SPEED"` | 顶层 `enum` 白名单 |
| `PAUSE.params` 里夹带 `steer_deg` | `additionalProperties: false` |
| `result = ACCEPTED` 却带 `reject_code` | 条件式 `if/then` |
| `report_kind = SAFETY_EVENT` 但 `safety = null` | 条件式 `if/then` |
| `brake_latency_ms = 150` | `maximum: 100` |
| 中止的复核标记 `verify_success = true` | 条件式 `if/then` |
| `is_suspect = true` 但 `event_id = null` | 条件式 `if/then` |

`additionalProperties: false` 这条尤其重要。它意味着任何人想往协议里塞一个转向角、一个速度设定值，报文当场就过不了校验，不需要靠代码评审去发现。安全边界的第一层由此从"约定"变成了"机器强制"。

运行：

```bash
pip install jsonschema
python3 validate.py
```

D3 评审前这个脚本必须全绿，输出：

```
PASS  Schema 5 份、正例 6 条、反例 9 条、内嵌副本一致、算例与预算全部自洽
```

建议接进 CI，每次改 Schema 自动跑一遍。

### 10.2 M1 评审 checklist

评审时逐条确认，缺一项不算通过。

**接口定义**

- [ ] 五份 Schema 文件齐全，`validate.py` 全绿
- [ ] 四条接口的字段表与 Schema 逐字段对应，没有"文档有 Schema 没有"或反过来的情况
- [ ] 每条接口至少一条示例报文，且能通过校验
- [ ] 附录 B 的枚举全集与各 Schema 内的 `enum` 一致

**安全边界**

- [ ] 白名单六条指令，协议里确认不存在转向角、轮速、扭矩、制动力、目标速度
- [ ] 网关的参数范围表已硬编码在网关源码里，评审时打开源码核对，不接受"在配置文件里"
- [ ] 越界处理是拒绝不是截断
- [ ] 心跳 5 Hz / 超时 1500 ms / 超时动作为 `RESUME`，三项在网关代码里可见
- [ ] 底盘安全层独立性由硬件组书面确认

**驱动层**

- [ ] 四个抽象基类签名冻结
- [ ] 非阻塞语义在桩实现里已验证
- [ ] `capabilities()` 开机自检已实现：把 `ptz_stub.max_zoom` 改成 2.0，系统必须拒绝启动
- [ ] 除 `factory.py` 外全仓库搜不到 `if mode == "stub"`

**桩**

- [ ] 三个桩跑起来，`stub.yaml` 的每个注入项都能生效
- [ ] `chassis_stub` 的 ACK 丢失、安全事件、`ESTOP` 三条路径各跑通一次
- [ ] `ptz_stub` 的有效像素比 $k = \min(1, 2/z)$ 已在代码里实现，桩上 $d_{\max} = 4.16$ m 已写进标定素材要求
- [ ] `pose_stub` 的失锁注入能触发 `POSE_INVALID` 抑制

**未决事项**

- [ ] 底盘是否提供任务级接口，已有结论（这条不确认，IF-2 无法冻结）
- [ ] 云台变焦是否达到 3× 光学，已有结论（这条不确认，像素密度判据失去支点）
- [ ] 首版缺陷类别是否收窄到 3 类，已有结论（影响附录 B.1 的枚举范围）

后两条未决事项直接决定接口能不能冻结。若云台只能做到 2× 光学，$p$ 在 5 m 处只有 99.8 px，达不到 120 px 的读数下限，要么把巡检位距离压到 4.2 m 以内，要么放弃指针表的自动读数改为只抓图人工判读。这是方案层面的改动，不是接口层面能兜住的，需要在 D3 之前定下来。

---

## 附录 A　拒绝码

| `reject_code` | 触发条件 | `mission` 的应对 |
|---|---|---|
| `NOT_IN_WHITELIST` | `command` 不在六条之内 | 代码 bug，记 CRITICAL 日志，进 `ABORT` |
| `SCHEMA_INVALID` | 报文结构不合法 | 同上 |
| `SCHEMA_VERSION_MISMATCH` | 主版本号不一致 | 停止发送指令，告警，等人工处理 |
| `PARAM_MISSING` | 必填参数缺失 | 代码 bug，进 `ABORT` |
| `PARAM_OUT_OF_RANGE` | 参数越界 | 代码 bug 或标定数据错，进 `ABORT` 并记下具体参数 |
| `UNKNOWN_WAYPOINT` | 巡检位不在网关标定表内 | 检查标定表版本，进 `ABORT` |
| `STATE_CONFLICT` | 当前底盘状态不接受该指令 | 重试一次，仍失败进 `ABORT` |
| `SAFETY_OVERRIDE` | 底盘安全层正在介入 | 立即进 `ABORT`，不重试 |
| `HEARTBEAT_LOST` | 看门狗已介入 | 恢复心跳，等看门狗解除 |
| `DRIVER_NOT_READY` | 驱动未初始化或已断开 | 进 `ABORT`，告警 |
| `DRIVER_TIMEOUT` | 底层通信超时 | 重试一次，仍失败进 `ABORT` |
| `ESTOP_ACTIVE` | 急停被按下 | 进 `ABORT`，不重试，等人工解除 |

只有 `STATE_CONFLICT` 和 `DRIVER_TIMEOUT` 允许重试，且只重试一次。其余全部直接中止。被拒绝的指令反复重试是最容易写出来的死循环，接口层面直接禁掉。

## 附录 B　枚举全集

### B.1 `defect_class` 与 severity 查表

| 枚举值 | 含义 | `severity` | 首版是否纳入 |
|---|---|---|---|
| `PRESSURE_GAUGE` | 压力表（读数） | 0.70 | 建议纳入 |
| `INDICATOR_LIGHT` | 指示灯（状态） | 0.50 | 建议纳入 |
| `OIL_LEAK` | 渗漏油 | 0.90 | 建议纳入 |
| `OIL_LEVEL_GAUGE` | 油位计 | 0.60 | 二期 |
| `SWITCH_HANDLE` | 开关把手位置 | 0.80 | 二期 |
| `INSULATOR_BREAK` | 绝缘子破损 | 0.95 | 二期 |
| `RUST_CORROSION` | 锈蚀 | 0.40 | 二期 |
| `FOREIGN_OBJECT` | 异物 | 0.60 | 二期 |
| `DOOR_OPEN` | 柜门未闭合 | 0.50 | 二期 |
| `CABLE_LOOSE` | 接线松动 | 0.85 | 二期 |

首版三类的挑选依据：一个走 L2 读数通路（压力表）、一个走 L2 分类通路（指示灯）、一个走纯 L1 检测通路（渗漏油）。三类各自打通一条链路，比十类都做半吊子更能证明架构成立。这也正好对应待拍板事项里"收窄到 3 类"的建议。

Schema 里的 `enum` 保留全部十类，收窄只在训练与验收层面执行。这样二期加类别不需要改接口。

### B.2 状态机状态

`CRUISE` `SUSPECT` `HALT_REQ` `AIM` `ZOOM` `CAPTURE` `VERIFY` `PACK` `RESUME` `ABORT`

### B.3 安全事件类型

| 枚举值 | 来源 | 默认 severity |
|---|---|---|
| `OBSTACLE_DETECTED` | 底盘安全层 | CRITICAL |
| `BUMPER_HIT` | 底盘安全层 | CRITICAL |
| `ESTOP_PRESSED` | 底盘安全层 | CRITICAL |
| `TILT_LIMIT` | 底盘安全层 | CRITICAL |
| `MOTOR_FAULT` | 驱动 | CRITICAL |
| `LOW_BATTERY` | 底盘 | WARN |
| `LOCALIZATION_LOST` | 驱动 | WARN |
| `HEARTBEAT_LOST` | 网关 | WARN |
| `ILLEGAL_COMMAND` | 网关 | WARN |
| `SCHEMA_VERSION_MISMATCH` | 网关 | CRITICAL |
| `COMM_LOST` | 网关 | CRITICAL |

### B.4 其他枚举

| 字段 | 取值 |
|---|---|
| `stage` | `CRUISE` `VERIFY` |
| `trigger_rule` | `CONF_BAND` `L2_UNREADABLE` `L2_OUT_OF_BAND` `L3_ANOMALY` `MANUAL` |
| `suppressed_by` | `TRACK_COOLDOWN` `WAYPOINT_ONCE` `RESUME_SILENCE` `BUDGET_EXHAUSTED` `POSE_INVALID` |
| `l2_reading.kind` | `POINTER_GAUGE` `DIGITAL_DISPLAY` `INDICATOR_LIGHT` `SWITCH_POSITION` |
| `chassis.state` | `MOVING` `STOPPING` `STOPPED` `PAUSED` `RETURNING` `FAULT` `ESTOP` |
| `focus_state` | `FOCUSING` `LOCKED` `FAILED` |
| `pose.source` | `LIDAR_SLAM` `ODOM_ONLY` `LOST` |
| `issued_by` | `MISSION_FSM` `CLOUD_MANUAL` `WATCHDOG` |
| `result`（ACK） | `ACCEPTED` `REJECTED` `PREEMPTED` |
| `progress` | `IN_PROGRESS` `DONE` `FAILED` `PREEMPTED` |
| `action_taken` | `NONE` `BRAKE` `ABORT_VERIFY` `FORCE_RESUME` `RETURN_HOME` |
| `verdict.result` | `CONFIRMED_DEFECT` `FALSE_ALARM` `READING_OK` `READING_ABNORMAL` `UNKNOWN_ANOMALY` `INCONCLUSIVE` |
| `files[].role` | `CRUISE_ANNOTATED` `CRUISE_RAW` `VERIFY_FRAME` `VERIFY_ROI` `ANOMALY_HEATMAP` `META_LOG` |
| `abort.reason` | `STATE_TIMEOUT` `SAFETY_EVENT` `ESTOP` `DRIVER_ERROR` `POSE_INVALID` `CLOUD_CANCEL` |

## 附录 C　变更记录

| 版本 | 日期 | 变更 | 评审 |
|---|---|---|---|
| v1.0 | D3 | 首版，M1 冻结 | 待评审 |

---

## 附录 D　JSON Schema 全文

以下五份 Schema 由 `patrol/tools/sync_icd_appendix.py` 从 `patrol/schemas/` 直接
生成，`validate.py` 第 6 项会按 `json.loads` 后深比较校验（差异清单 D4：原文要求
「逐字节一致」，但 markdown 围栏缩进与行尾空白会让它误报，误报会训练出「红了就手工
改一下附录」的习惯，反而削弱这条检查）。

**改 Schema 之后跑一次 `python -m patrol.tools.sync_icd_appendix` 即可**，不要手工改本附录。

Draft 2020-12。所有对象都带 `additionalProperties: false`，未定义的字段一律不接受。
`evidence_package` 的 `l2_reading` 跨文件 `$ref` 了 `detection_event` 的定义（D3），
两份 Schema 的 `$id` 就是为此而设。

### D.1　`detection_event.schema.json`

IF-1　DetectionEvent

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://patrol.local/schemas/detection_event.schema.json",
  "title": "DetectionEvent",
  "type": "object",
  "required": [
    "schema_version",
    "msg_type",
    "seq",
    "ts_mono_ns",
    "ts_utc_ms",
    "run_id",
    "event_id",
    "stage",
    "model",
    "context",
    "detections",
    "suspect",
    "latency_ms"
  ],
  "additionalProperties": false,
  "properties": {
    "schema_version": {
      "type": "string",
      "pattern": "^\\d+\\.\\d+\\.\\d+$"
    },
    "msg_type": {
      "const": "DETECTION_EVENT"
    },
    "seq": {
      "type": "integer",
      "minimum": 0,
      "maximum": 4294967295
    },
    "ts_mono_ns": {
      "type": "integer",
      "minimum": 0
    },
    "ts_utc_ms": {
      "type": "integer",
      "minimum": 0
    },
    "run_id": {
      "type": "string",
      "pattern": "^\\d{8}-\\d{6}-[0-9a-f]{4}$"
    },
    "event_id": {
      "type": [
        "string",
        "null"
      ],
      "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    },
    "stage": {
      "enum": [
        "CRUISE",
        "VERIFY"
      ]
    },
    "model": {
      "type": "object",
      "required": [
        "name",
        "input_w",
        "input_h",
        "quant",
        "conf_threshold",
        "nms_iou"
      ],
      "additionalProperties": false,
      "properties": {
        "name": {
          "enum": [
            "yolo11s",
            "yolo11m"
          ]
        },
        "input_w": {
          "type": "integer",
          "minimum": 64,
          "maximum": 4096
        },
        "input_h": {
          "type": "integer",
          "minimum": 64,
          "maximum": 4096
        },
        "quant": {
          "enum": [
            "INT8",
            "FP16"
          ]
        },
        "conf_threshold": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        },
        "nms_iou": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        }
      }
    },
    "context": {
      "type": "object",
      "required": [
        "waypoint_id",
        "pose",
        "pose_valid",
        "speed_mps",
        "ptz",
        "image_w",
        "image_h"
      ],
      "additionalProperties": false,
      "properties": {
        "waypoint_id": {
          "type": [
            "string",
            "null"
          ],
          "pattern": "^WP-\\d{2}$"
        },
        "pose": {
          "type": "object",
          "required": [
            "x_m",
            "y_m",
            "yaw_deg",
            "cov_trace"
          ],
          "additionalProperties": false,
          "properties": {
            "x_m": {
              "type": "number"
            },
            "y_m": {
              "type": "number"
            },
            "yaw_deg": {
              "type": "number",
              "minimum": -180,
              "maximum": 180
            },
            "cov_trace": {
              "type": "number",
              "minimum": 0
            }
          }
        },
        "pose_valid": {
          "type": "boolean"
        },
        "speed_mps": {
          "type": "number",
          "minimum": 0,
          "maximum": 1.5
        },
        "ptz": {
          "type": "object",
          "required": [
            "pan_deg",
            "tilt_deg",
            "zoom",
            "hfov_deg"
          ],
          "additionalProperties": false,
          "properties": {
            "pan_deg": {
              "type": "number",
              "minimum": -170,
              "maximum": 170
            },
            "tilt_deg": {
              "type": "number",
              "minimum": -30,
              "maximum": 60
            },
            "zoom": {
              "type": "number",
              "minimum": 1,
              "maximum": 3
            },
            "hfov_deg": {
              "type": "number",
              "exclusiveMinimum": 0,
              "maximum": 180
            }
          }
        },
        "image_w": {
          "type": "integer",
          "minimum": 1
        },
        "image_h": {
          "type": "integer",
          "minimum": 1
        }
      }
    },
    "detections": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "track_id",
          "defect_class",
          "confidence",
          "bbox",
          "target_size_m",
          "est_distance_m",
          "pixel_density_px",
          "aim_offset",
          "l2_reading"
        ],
        "additionalProperties": false,
        "properties": {
          "track_id": {
            "type": "integer",
            "minimum": 0
          },
          "defect_class": {
            "$ref": "#/$defs/defectClass"
          },
          "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1
          },
          "bbox": {
            "type": "array",
            "items": {
              "type": "number",
              "minimum": 0
            },
            "minItems": 4,
            "maxItems": 4
          },
          "target_size_m": {
            "type": "number",
            "exclusiveMinimum": 0
          },
          "est_distance_m": {
            "type": "number",
            "exclusiveMinimum": 0
          },
          "pixel_density_px": {
            "type": "number",
            "minimum": 0
          },
          "aim_offset": {
            "type": "object",
            "required": [
              "pan_deg",
              "tilt_deg"
            ],
            "additionalProperties": false,
            "properties": {
              "pan_deg": {
                "type": "number"
              },
              "tilt_deg": {
                "type": "number"
              }
            }
          },
          "l2_reading": {
            "$ref": "#/$defs/reading"
          },
          "quality": {
            "$comment": "差异清单 A4 增补的可选字段（ICD 冻结规则：新增可选字段=次版本号+1，通知即可）。方案书 §6.4 的四项质量指标，ICD v1.0 遗漏。",
            "oneOf": [
              {
                "type": "null"
              },
              {
                "type": "object",
                "required": [
                  "pixel_density_px",
                  "pixel_density",
                  "blur",
                  "highlight",
                  "occlusion",
                  "score"
                ],
                "additionalProperties": false,
                "properties": {
                  "pixel_density_px": {
                    "type": "number",
                    "minimum": 0
                  },
                  "pixel_density": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1
                  },
                  "blur": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1
                  },
                  "highlight": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1
                  },
                  "occlusion": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1
                  },
                  "score": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1
                  }
                }
              }
            ]
          }
        }
      }
    },
    "l3_anomaly": {
      "oneOf": [
        {
          "type": "null"
        },
        {
          "type": "object",
          "required": [
            "model",
            "anomaly_score",
            "threshold",
            "is_anomaly",
            "heatmap_ref"
          ],
          "additionalProperties": false,
          "properties": {
            "model": {
              "type": "string"
            },
            "anomaly_score": {
              "type": "number",
              "minimum": 0,
              "maximum": 1
            },
            "threshold": {
              "type": "number",
              "minimum": 0,
              "maximum": 1
            },
            "is_anomaly": {
              "type": "boolean"
            },
            "heatmap_ref": {
              "type": [
                "string",
                "null"
              ]
            }
          }
        }
      ]
    },
    "suspect": {
      "type": "object",
      "required": [
        "is_suspect",
        "trigger_rule",
        "target_track_id",
        "severity",
        "novelty",
        "priority",
        "suppressed_by"
      ],
      "additionalProperties": false,
      "properties": {
        "is_suspect": {
          "type": "boolean"
        },
        "trigger_rule": {
          "oneOf": [
            {
              "type": "null"
            },
            {
              "enum": [
                "CONF_BAND",
                "L2_UNREADABLE",
                "L2_OUT_OF_BAND",
                "L3_ANOMALY",
                "MANUAL",
                "QUALITY_LOW"
              ]
            }
          ]
        },
        "target_track_id": {
          "type": [
            "integer",
            "null"
          ],
          "minimum": 0
        },
        "severity": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        },
        "novelty": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        },
        "priority": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        },
        "suppressed_by": {
          "oneOf": [
            {
              "type": "null"
            },
            {
              "enum": [
                "TRACK_COOLDOWN",
                "WAYPOINT_ONCE",
                "RESUME_SILENCE",
                "BUDGET_EXHAUSTED",
                "POSE_INVALID"
              ]
            }
          ]
        }
      }
    },
    "latency_ms": {
      "type": "object",
      "required": [
        "capture_to_infer",
        "infer",
        "postproc",
        "total"
      ],
      "additionalProperties": false,
      "properties": {
        "capture_to_infer": {
          "type": "integer",
          "minimum": 0
        },
        "infer": {
          "type": "integer",
          "minimum": 0
        },
        "postproc": {
          "type": "integer",
          "minimum": 0
        },
        "total": {
          "type": "integer",
          "minimum": 0
        }
      }
    }
  },
  "allOf": [
    {
      "if": {
        "properties": {
          "suspect": {
            "properties": {
              "is_suspect": {
                "const": true
              }
            }
          }
        }
      },
      "then": {
        "properties": {
          "event_id": {
            "type": "string"
          },
          "suspect": {
            "required": [
              "trigger_rule"
            ],
            "properties": {
              "trigger_rule": {
                "type": "string"
              }
            }
          }
        }
      }
    }
  ],
  "$defs": {
    "defectClass": {
      "enum": [
        "PRESSURE_GAUGE",
        "OIL_LEVEL_GAUGE",
        "INDICATOR_LIGHT",
        "SWITCH_HANDLE",
        "INSULATOR_BREAK",
        "OIL_LEAK",
        "RUST_CORROSION",
        "FOREIGN_OBJECT",
        "DOOR_OPEN",
        "CABLE_LOOSE"
      ]
    },
    "reading": {
      "oneOf": [
        {
          "type": "null"
        },
        {
          "type": "object",
          "required": [
            "kind",
            "value",
            "unit",
            "range_min",
            "range_max",
            "in_normal_band",
            "reading_confidence",
            "roi"
          ],
          "additionalProperties": false,
          "properties": {
            "kind": {
              "enum": [
                "POINTER_GAUGE",
                "DIGITAL_DISPLAY",
                "INDICATOR_LIGHT",
                "SWITCH_POSITION"
              ]
            },
            "value": {
              "type": [
                "number",
                "string",
                "null"
              ]
            },
            "unit": {
              "type": [
                "string",
                "null"
              ]
            },
            "range_min": {
              "type": [
                "number",
                "null"
              ]
            },
            "range_max": {
              "type": [
                "number",
                "null"
              ]
            },
            "in_normal_band": {
              "type": [
                "boolean",
                "null"
              ]
            },
            "reading_confidence": {
              "type": "number",
              "minimum": 0,
              "maximum": 1
            },
            "roi": {
              "type": "array",
              "items": {
                "type": "number",
                "minimum": 0
              },
              "minItems": 4,
              "maxItems": 4
            }
          }
        }
      ],
      "$comment": "D3：提到 $defs.reading 供 evidence_package 跨文件 $ref 复用。证据包是要进台账、回流训练集的最终产物，它的读数结构不该比中间报文校验更松。"
    }
  }
}
```

### D.2　`control_command.schema.json`

IF-2　ControlCommand

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://patrol.local/schemas/control_command.schema.json",
  "title": "ControlCommand",
  "type": "object",
  "required": [
    "schema_version",
    "msg_type",
    "cmd_id",
    "seq",
    "ts_mono_ns",
    "ts_utc_ms",
    "run_id",
    "event_id",
    "issued_by",
    "command",
    "params",
    "timeout_ms"
  ],
  "additionalProperties": false,
  "properties": {
    "schema_version": {
      "type": "string",
      "pattern": "^\\d+\\.\\d+\\.\\d+$"
    },
    "msg_type": {
      "const": "CONTROL_COMMAND"
    },
    "cmd_id": {
      "$ref": "#/$defs/uuid"
    },
    "seq": {
      "type": "integer",
      "minimum": 0,
      "maximum": 4294967295
    },
    "ts_mono_ns": {
      "type": "integer",
      "minimum": 0
    },
    "ts_utc_ms": {
      "type": "integer",
      "minimum": 0
    },
    "run_id": {
      "type": "string",
      "pattern": "^\\d{8}-\\d{6}-[0-9a-f]{4}$"
    },
    "event_id": {
      "oneOf": [
        {
          "type": "null"
        },
        {
          "$ref": "#/$defs/uuid"
        }
      ]
    },
    "issued_by": {
      "enum": [
        "MISSION_FSM",
        "CLOUD_MANUAL",
        "WATCHDOG"
      ]
    },
    "command": {
      "enum": [
        "PAUSE",
        "RESUME",
        "CREEP_FORWARD",
        "GOTO_OBSERVE",
        "PTZ_SET",
        "PTZ_RATE",
        "HEARTBEAT"
      ]
    },
    "params": {
      "type": "object"
    },
    "timeout_ms": {
      "type": "integer",
      "minimum": 1,
      "maximum": 30000
    }
  },
  "allOf": [
    {
      "if": {
        "properties": {
          "command": {
            "const": "PAUSE"
          }
        },
        "required": [
          "command"
        ]
      },
      "then": {
        "properties": {
          "params": {
            "$ref": "#/$defs/pausePar"
          }
        }
      }
    },
    {
      "if": {
        "properties": {
          "command": {
            "const": "RESUME"
          }
        },
        "required": [
          "command"
        ]
      },
      "then": {
        "properties": {
          "params": {
            "type": "object",
            "additionalProperties": false,
            "properties": {}
          }
        }
      }
    },
    {
      "if": {
        "properties": {
          "command": {
            "const": "CREEP_FORWARD"
          }
        },
        "required": [
          "command"
        ]
      },
      "then": {
        "properties": {
          "params": {
            "$ref": "#/$defs/creepPar"
          }
        }
      }
    },
    {
      "if": {
        "properties": {
          "command": {
            "const": "GOTO_OBSERVE"
          }
        },
        "required": [
          "command"
        ]
      },
      "then": {
        "properties": {
          "params": {
            "$ref": "#/$defs/gotoPar"
          }
        }
      }
    },
    {
      "if": {
        "properties": {
          "command": {
            "const": "PTZ_SET"
          }
        },
        "required": [
          "command"
        ]
      },
      "then": {
        "properties": {
          "params": {
            "$ref": "#/$defs/ptzPar"
          }
        }
      }
    },
    {
      "if": {
        "properties": {
          "command": {
            "const": "PTZ_RATE"
          }
        },
        "required": [
          "command"
        ]
      },
      "then": {
        "properties": {
          "params": {
            "$ref": "#/$defs/ratePar"
          }
        }
      }
    },
    {
      "if": {
        "properties": {
          "command": {
            "const": "HEARTBEAT"
          }
        },
        "required": [
          "command"
        ]
      },
      "then": {
        "properties": {
          "params": {
            "$ref": "#/$defs/hbPar"
          }
        }
      }
    }
  ],
  "$defs": {
    "uuid": {
      "type": "string",
      "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    },
    "pausePar": {
      "type": "object",
      "required": [
        "reason"
      ],
      "additionalProperties": false,
      "properties": {
        "reason": {
          "enum": [
            "VERIFY_REQUEST",
            "CLOUD_MANUAL",
            "WATCHDOG_RECOVER"
          ]
        }
      }
    },
    "creepPar": {
      "type": "object",
      "required": [
        "distance_m"
      ],
      "additionalProperties": false,
      "properties": {
        "distance_m": {
          "type": "number",
          "minimum": 0.05,
          "maximum": 0.5
        }
      }
    },
    "gotoPar": {
      "type": "object",
      "required": [
        "waypoint_id",
        "tolerance_m"
      ],
      "additionalProperties": false,
      "properties": {
        "waypoint_id": {
          "type": "string",
          "pattern": "^WP-\\d{2}$"
        },
        "tolerance_m": {
          "type": "number",
          "minimum": 0.1,
          "maximum": 0.5
        }
      }
    },
    "ptzPar": {
      "type": "object",
      "required": [
        "pan_deg",
        "tilt_deg",
        "zoom",
        "speed"
      ],
      "additionalProperties": false,
      "properties": {
        "pan_deg": {
          "type": "number",
          "minimum": -170.0,
          "maximum": 170.0
        },
        "tilt_deg": {
          "type": "number",
          "minimum": -30.0,
          "maximum": 60.0
        },
        "zoom": {
          "type": "number",
          "minimum": 1.0,
          "maximum": 3.0
        },
        "speed": {
          "enum": [
            "SLOW",
            "NORMAL"
          ]
        }
      }
    },
    "ratePar": {
      "type": "object",
      "required": [
        "pan_dps",
        "tilt_dps",
        "ttl_ms"
      ],
      "additionalProperties": false,
      "properties": {
        "pan_dps": {
          "type": "number",
          "minimum": -60.0,
          "maximum": 60.0
        },
        "tilt_dps": {
          "type": "number",
          "minimum": -40.0,
          "maximum": 40.0
        },
        "ttl_ms": {
          "type": "integer",
          "minimum": 100,
          "maximum": 500
        }
      },
      "$comment": "A1：云台速率闭环。ttl_ms 是自失效时长——超过它没有新指令刷新，网关把云台速度归零，防止 mission 崩溃时云台一直转到限位。"
    },
    "hbPar": {
      "type": "object",
      "required": [
        "mission_state"
      ],
      "additionalProperties": false,
      "properties": {
        "mission_state": {
          "enum": [
            "CRUISE",
            "SUSPECT",
            "HALT_REQ",
            "AIM",
            "ZOOM",
            "CAPTURE",
            "VERIFY",
            "PACK",
            "RESUME",
            "ABORT"
          ]
        }
      }
    }
  }
}
```

### D.3　`command_ack.schema.json`

IF-2　CommandAck

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://patrol.local/schemas/command_ack.schema.json",
  "title": "CommandAck",
  "type": "object",
  "required": [
    "schema_version",
    "msg_type",
    "cmd_id",
    "ts_mono_ns",
    "result",
    "reject_code",
    "reject_detail",
    "checks",
    "exec_handle"
  ],
  "additionalProperties": false,
  "properties": {
    "schema_version": {
      "type": "string",
      "pattern": "^\\d+\\.\\d+\\.\\d+$"
    },
    "msg_type": {
      "const": "COMMAND_ACK"
    },
    "cmd_id": {
      "type": "string",
      "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    },
    "ts_mono_ns": {
      "type": "integer",
      "minimum": 0
    },
    "result": {
      "enum": [
        "ACCEPTED",
        "REJECTED",
        "PREEMPTED"
      ]
    },
    "reject_code": {
      "oneOf": [
        {
          "type": "null"
        },
        {
          "enum": [
            "NOT_IN_WHITELIST",
            "SCHEMA_INVALID",
            "SCHEMA_VERSION_MISMATCH",
            "PARAM_MISSING",
            "PARAM_OUT_OF_RANGE",
            "UNKNOWN_WAYPOINT",
            "STATE_CONFLICT",
            "SAFETY_OVERRIDE",
            "HEARTBEAT_LOST",
            "DRIVER_NOT_READY",
            "DRIVER_TIMEOUT",
            "ESTOP_ACTIVE"
          ]
        }
      ]
    },
    "reject_detail": {
      "type": [
        "string",
        "null"
      ],
      "maxLength": 256
    },
    "checks": {
      "type": "object",
      "required": [
        "whitelist",
        "schema",
        "range",
        "state_conflict",
        "safety_override"
      ],
      "additionalProperties": false,
      "properties": {
        "whitelist": {
          "$ref": "#/$defs/check"
        },
        "schema": {
          "$ref": "#/$defs/check"
        },
        "range": {
          "$ref": "#/$defs/check"
        },
        "state_conflict": {
          "$ref": "#/$defs/check"
        },
        "safety_override": {
          "$ref": "#/$defs/check"
        }
      }
    },
    "exec_handle": {
      "type": [
        "string",
        "null"
      ]
    }
  },
  "allOf": [
    {
      "if": {
        "properties": {
          "result": {
            "const": "ACCEPTED"
          }
        },
        "required": [
          "result"
        ]
      },
      "then": {
        "properties": {
          "reject_code": {
            "type": "null"
          },
          "exec_handle": {
            "type": "string"
          }
        }
      }
    },
    {
      "if": {
        "properties": {
          "result": {
            "const": "REJECTED"
          }
        },
        "required": [
          "result"
        ]
      },
      "then": {
        "properties": {
          "reject_code": {
            "type": "string"
          },
          "exec_handle": {
            "type": "null"
          }
        }
      }
    },
    {
      "if": {
        "properties": {
          "result": {
            "const": "PREEMPTED"
          }
        },
        "required": [
          "result"
        ]
      },
      "then": {
        "properties": {
          "reject_code": {
            "type": "null"
          },
          "exec_handle": {
            "type": "string"
          }
        }
      },
      "$comment": "D2：PREEMPTED 表示指令被更高优先级动作打断（ICD §4.4），语义上不是拒绝，不应带 reject_code。原来只写了 ACCEPTED / REJECTED 两条，于是一条同时带 reject_code 和 exec_handle 的 PREEMPTED 会被放行。"
    }
  ],
  "$defs": {
    "check": {
      "enum": [
        "PASS",
        "FAIL",
        "SKIP"
      ]
    }
  }
}
```

### D.4　`status_report.schema.json`

IF-3　StatusReport

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://patrol.local/schemas/status_report.schema.json",
  "title": "StatusReport",
  "type": "object",
  "required": [
    "schema_version",
    "msg_type",
    "seq",
    "ts_mono_ns",
    "ts_utc_ms",
    "run_id",
    "report_kind",
    "chassis",
    "ptz",
    "pose",
    "watchdog"
  ],
  "additionalProperties": false,
  "properties": {
    "schema_version": {
      "type": "string",
      "pattern": "^\\d+\\.\\d+\\.\\d+$"
    },
    "msg_type": {
      "const": "STATUS_REPORT"
    },
    "seq": {
      "type": "integer",
      "minimum": 0,
      "maximum": 4294967295
    },
    "ts_mono_ns": {
      "type": "integer",
      "minimum": 0
    },
    "ts_utc_ms": {
      "type": "integer",
      "minimum": 0
    },
    "run_id": {
      "type": "string",
      "pattern": "^\\d{8}-\\d{6}-[0-9a-f]{4}$"
    },
    "report_kind": {
      "enum": [
        "PERIODIC",
        "SAFETY_EVENT",
        "EXEC_UPDATE"
      ]
    },
    "chassis": {
      "type": "object",
      "required": [
        "state",
        "speed_mps",
        "path_progress",
        "distance_to_goal_m",
        "current_waypoint_id",
        "battery_pct",
        "safety_layer_active"
      ],
      "additionalProperties": false,
      "properties": {
        "state": {
          "enum": [
            "MOVING",
            "STOPPING",
            "STOPPED",
            "PAUSED",
            "RETURNING",
            "FAULT",
            "ESTOP"
          ]
        },
        "speed_mps": {
          "type": "number",
          "minimum": 0,
          "maximum": 1.5
        },
        "path_progress": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        },
        "distance_to_goal_m": {
          "type": [
            "number",
            "null"
          ],
          "minimum": 0
        },
        "current_waypoint_id": {
          "type": [
            "string",
            "null"
          ],
          "pattern": "^WP-\\d{2}$"
        },
        "battery_pct": {
          "type": "number",
          "minimum": 0,
          "maximum": 100
        },
        "safety_layer_active": {
          "type": "boolean"
        }
      }
    },
    "ptz": {
      "type": "object",
      "required": [
        "pan_deg",
        "tilt_deg",
        "zoom",
        "hfov_deg",
        "moving",
        "focus_state",
        "at_target"
      ],
      "additionalProperties": false,
      "properties": {
        "pan_deg": {
          "type": "number",
          "minimum": -170,
          "maximum": 170
        },
        "tilt_deg": {
          "type": "number",
          "minimum": -30,
          "maximum": 60
        },
        "zoom": {
          "type": "number",
          "minimum": 1,
          "maximum": 3
        },
        "hfov_deg": {
          "type": "number",
          "exclusiveMinimum": 0,
          "maximum": 180
        },
        "moving": {
          "type": "boolean"
        },
        "focus_state": {
          "enum": [
            "FOCUSING",
            "LOCKED",
            "FAILED"
          ]
        },
        "at_target": {
          "type": "boolean"
        }
      }
    },
    "pose": {
      "type": "object",
      "required": [
        "x_m",
        "y_m",
        "yaw_deg",
        "cov_trace",
        "valid",
        "source"
      ],
      "additionalProperties": false,
      "properties": {
        "x_m": {
          "type": "number"
        },
        "y_m": {
          "type": "number"
        },
        "yaw_deg": {
          "type": "number",
          "minimum": -180,
          "maximum": 180
        },
        "cov_trace": {
          "type": "number",
          "minimum": 0
        },
        "valid": {
          "type": "boolean"
        },
        "source": {
          "enum": [
            "LIDAR_SLAM",
            "ODOM_ONLY",
            "LOST"
          ]
        }
      }
    },
    "watchdog": {
      "type": "object",
      "required": [
        "heartbeat_ok",
        "last_heartbeat_age_ms",
        "watchdog_triggered"
      ],
      "additionalProperties": false,
      "properties": {
        "heartbeat_ok": {
          "type": "boolean"
        },
        "last_heartbeat_age_ms": {
          "type": "integer",
          "minimum": 0
        },
        "watchdog_triggered": {
          "type": "boolean"
        }
      }
    },
    "exec": {
      "oneOf": [
        {
          "type": "null"
        },
        {
          "type": "object",
          "required": [
            "exec_handle",
            "cmd_id",
            "progress",
            "elapsed_ms",
            "fail_reason"
          ],
          "additionalProperties": false,
          "properties": {
            "exec_handle": {
              "type": "string"
            },
            "cmd_id": {
              "type": "string",
              "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
            },
            "progress": {
              "enum": [
                "IN_PROGRESS",
                "DONE",
                "FAILED",
                "PREEMPTED"
              ]
            },
            "elapsed_ms": {
              "type": "integer",
              "minimum": 0
            },
            "fail_reason": {
              "type": [
                "string",
                "null"
              ]
            }
          }
        }
      ]
    },
    "safety": {
      "oneOf": [
        {
          "type": "null"
        },
        {
          "type": "object",
          "required": [
            "event_type",
            "severity",
            "source",
            "action_taken",
            "brake_latency_ms",
            "detail"
          ],
          "additionalProperties": false,
          "properties": {
            "event_type": {
              "enum": [
                "OBSTACLE_DETECTED",
                "BUMPER_HIT",
                "ESTOP_PRESSED",
                "TILT_LIMIT",
                "MOTOR_FAULT",
                "LOW_BATTERY",
                "LOCALIZATION_LOST",
                "HEARTBEAT_LOST",
                "ILLEGAL_COMMAND",
                "SCHEMA_VERSION_MISMATCH",
                "COMM_LOST"
              ]
            },
            "severity": {
              "enum": [
                "INFO",
                "WARN",
                "CRITICAL"
              ]
            },
            "source": {
              "enum": [
                "CHASSIS_SAFETY_LAYER",
                "GATEWAY",
                "DRIVER"
              ]
            },
            "action_taken": {
              "enum": [
                "NONE",
                "BRAKE",
                "ABORT_VERIFY",
                "FORCE_RESUME",
                "RETURN_HOME"
              ]
            },
            "brake_latency_ms": {
              "type": [
                "integer",
                "null"
              ],
              "minimum": 0,
              "maximum": 5000,
              "$comment": "D1：这是底盘报上来的**实测值**，不是指令参数。上限焊在验收指标 100 ms 上会让『制动超标』这条报文整条解析失败，恰好丢掉最该留证的证据。Schema 只挡明显非法的量级，100 ms 的验收判定由网关按 limits.BRAKE_LATENCY_LIMIT_MS 做逻辑判断并抛 SafetyEvent。"
            },
            "detail": {
              "type": "string",
              "maxLength": 256
            }
          }
        }
      ]
    }
  },
  "allOf": [
    {
      "if": {
        "properties": {
          "report_kind": {
            "const": "SAFETY_EVENT"
          }
        },
        "required": [
          "report_kind"
        ]
      },
      "then": {
        "required": [
          "safety"
        ],
        "properties": {
          "safety": {
            "type": "object"
          }
        }
      }
    },
    {
      "if": {
        "properties": {
          "report_kind": {
            "const": "EXEC_UPDATE"
          }
        },
        "required": [
          "report_kind"
        ]
      },
      "then": {
        "required": [
          "exec"
        ],
        "properties": {
          "exec": {
            "type": "object"
          }
        }
      }
    }
  ]
}
```

### D.5　`evidence_package.schema.json`

IF-4　EvidencePackage

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://patrol.local/schemas/evidence_package.schema.json",
  "title": "EvidencePackage",
  "type": "object",
  "required": [
    "schema_version",
    "msg_type",
    "run_id",
    "event_id",
    "waypoint_id",
    "ts_utc_ms",
    "verdict",
    "before",
    "after",
    "gain",
    "timeline",
    "files",
    "abort"
  ],
  "additionalProperties": false,
  "properties": {
    "schema_version": {
      "type": "string",
      "pattern": "^\\d+\\.\\d+\\.\\d+$"
    },
    "msg_type": {
      "const": "EVIDENCE_PACKAGE"
    },
    "run_id": {
      "type": "string",
      "pattern": "^\\d{8}-\\d{6}-[0-9a-f]{4}$"
    },
    "event_id": {
      "type": "string",
      "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    },
    "waypoint_id": {
      "type": "string",
      "pattern": "^WP-\\d{2}$"
    },
    "ts_utc_ms": {
      "type": "integer",
      "minimum": 0
    },
    "verdict": {
      "type": "object",
      "required": [
        "result",
        "defect_class",
        "severity",
        "needs_human_review",
        "confidence"
      ],
      "additionalProperties": false,
      "properties": {
        "result": {
          "enum": [
            "CONFIRMED_DEFECT",
            "FALSE_ALARM",
            "READING_OK",
            "READING_ABNORMAL",
            "UNKNOWN_ANOMALY",
            "INCONCLUSIVE"
          ]
        },
        "defect_class": {
          "type": [
            "string",
            "null"
          ]
        },
        "severity": {
          "enum": [
            "INFO",
            "WARN",
            "CRITICAL"
          ]
        },
        "needs_human_review": {
          "type": "boolean"
        },
        "confidence": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        }
      }
    },
    "before": {
      "$ref": "#/$defs/snapshot"
    },
    "after": {
      "$ref": "#/$defs/snapshot"
    },
    "gain": {
      "type": "object",
      "required": [
        "delta_conf",
        "pixel_density_ratio",
        "verify_success"
      ],
      "additionalProperties": false,
      "properties": {
        "delta_conf": {
          "type": "number",
          "minimum": -1,
          "maximum": 1
        },
        "pixel_density_ratio": {
          "type": "number",
          "minimum": 0
        },
        "verify_success": {
          "type": "boolean"
        }
      }
    },
    "timeline": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "state",
          "duration_ms"
        ],
        "additionalProperties": false,
        "properties": {
          "state": {
            "enum": [
              "SUSPECT",
              "HALT_REQ",
              "AIM",
              "ZOOM",
              "CAPTURE",
              "VERIFY",
              "PACK",
              "RESUME",
              "ABORT"
            ]
          },
          "duration_ms": {
            "type": "integer",
            "minimum": 0
          }
        }
      }
    },
    "files": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": [
          "path",
          "role",
          "bytes",
          "sha256",
          "uploaded"
        ],
        "additionalProperties": false,
        "properties": {
          "path": {
            "type": "string"
          },
          "role": {
            "enum": [
              "CRUISE_ANNOTATED",
              "CRUISE_RAW",
              "VERIFY_FRAME",
              "VERIFY_ROI",
              "ANOMALY_HEATMAP",
              "META_LOG",
              "VERIFY_FRAME_AUX",
              "CRUISE_VIDEO"
            ]
          },
          "bytes": {
            "type": "integer",
            "minimum": 0
          },
          "sha256": {
            "type": "string"
          },
          "uploaded": {
            "type": "boolean"
          }
        }
      }
    },
    "abort": {
      "oneOf": [
        {
          "type": "null"
        },
        {
          "type": "object",
          "required": [
            "at_state",
            "reason",
            "detail"
          ],
          "additionalProperties": false,
          "properties": {
            "at_state": {
              "enum": [
                "SUSPECT",
                "HALT_REQ",
                "AIM",
                "ZOOM",
                "CAPTURE",
                "VERIFY",
                "PACK",
                "RESUME"
              ]
            },
            "reason": {
              "enum": [
                "STATE_TIMEOUT",
                "SAFETY_EVENT",
                "ESTOP",
                "DRIVER_ERROR",
                "POSE_INVALID",
                "CLOUD_CANCEL"
              ]
            },
            "detail": {
              "type": "string",
              "maxLength": 256
            }
          }
        }
      ]
    }
  },
  "allOf": [
    {
      "if": {
        "properties": {
          "abort": {
            "type": "object"
          }
        },
        "required": [
          "abort"
        ]
      },
      "then": {
        "properties": {
          "gain": {
            "properties": {
              "verify_success": {
                "const": false
              }
            }
          }
        }
      }
    }
  ],
  "$defs": {
    "snapshot": {
      "type": "object",
      "required": [
        "confidence",
        "pixel_density_px",
        "zoom",
        "est_distance_m",
        "defect_class",
        "l2_reading"
      ],
      "additionalProperties": false,
      "properties": {
        "confidence": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        },
        "pixel_density_px": {
          "type": "number",
          "minimum": 0
        },
        "zoom": {
          "type": "number",
          "minimum": 1,
          "maximum": 3
        },
        "est_distance_m": {
          "type": "number",
          "exclusiveMinimum": 0
        },
        "defect_class": {
          "type": [
            "string",
            "null"
          ]
        },
        "l2_reading": {
          "$ref": "https://patrol.local/schemas/detection_event.schema.json#/$defs/reading",
          "$comment": "D3：复用 IF-1 的完整定义（8 个字段 + kind 枚举 + additionalProperties:false）。原来只写 {\"type\":[\"object\",\"null\"]}，往 after.l2_reading 塞 {\"kind\":\"NO_SUCH_KIND\",\"junk\":1} 会被放行。"
        },
        "multiview_spread": {
          "$comment": "A3：三视角读数极差（% FS），条件式辅视角启用时才有",
          "type": [
            "number",
            "null"
          ],
          "minimum": 0
        }
      }
    }
  }
}
```
