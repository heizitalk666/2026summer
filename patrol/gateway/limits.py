"""网关参数范围：硬编码常量。ICD §4.3。

**这个文件是安全边界的唯一真值，全仓库不允许有第二处定义。**

ICD §4.3 的硬性要求：这些值硬编码在网关进程内，网关不从 AI 侧下发的任何
配置里读取，也不读 perception 或 mission 的配置文件。修改需要改源码并重新
走 D3 级别的评审。§10.2 评审 checklist 明确写着"评审时打开源码核对，不接受
『在配置文件里』"。

所以：**本模块不 import config，也不接受任何运行时参数。**

同一组范围值也写在 patrol/schemas/*.json 里，那是报文层校验（拦非法报文），
本模块是执行层校验（拦非法动作），两层依据不同、都要有。差异清单 D5 指出
ICD 没说清两者如何保持同步，因此 tools/validate.py 增加了第 8 项检查：
交叉比对本模块的常量与 Schema 的 minimum/maximum，不一致即报错。
"""
from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------- 白名单
#: ICD v2.0 §4.1 七条指令。PTZ_RATE 由 D3 决议 A1 增补（云台速率闭环），
#: 它的"是否改变车辆运动"为否，不触碰底盘安全边界。是否接受由
#: gateway.node 按 enable_ptz_rate 开关决定，但常量在这里定死。
#:
#: WHITELIST 仍只有六条：**开关关掉时 PTZ_RATE 必须被拒**，
#: 这是"白名单增删要重新评审安全边界"那条规矩的落点。
WHITELIST: Final[frozenset[str]] = frozenset({
    "PAUSE", "RESUME", "CREEP_FORWARD", "GOTO_OBSERVE", "PTZ_SET", "HEARTBEAT",
})
WHITELIST_WITH_RATE: Final[frozenset[str]] = WHITELIST | {"PTZ_RATE"}

#: 协议中不存在这些量。AI 侧没有任何字段可以直接指定车怎么动。
#: 网关看到 params 里出现它们一律拒绝，即使 command 本身合法。
FORBIDDEN_PARAMS: Final[frozenset[str]] = frozenset({
    "steer_deg", "steering_angle", "wheel_speed", "wheel_rpm", "torque",
    "torque_nm", "brake_force", "brake_pct", "target_speed", "speed_mps",
    "throttle", "duty_cycle", "motor_current",
})

# ---------------------------------------------------------------- 数值范围
CREEP_DISTANCE_M: Final[tuple[float, float]] = (0.05, 0.50)
GOTO_TOLERANCE_M: Final[tuple[float, float]] = (0.10, 0.50)
PTZ_PAN_DEG: Final[tuple[float, float]] = (-170.0, 170.0)
PTZ_TILT_DEG: Final[tuple[float, float]] = (-30.0, 60.0)
PTZ_ZOOM: Final[tuple[float, float]] = (1.0, 3.0)
#: A1 速率通路。上限取 ptz_stub 默认转速，超过即拒。
PTZ_PAN_DPS: Final[tuple[float, float]] = (-60.0, 60.0)
PTZ_TILT_DPS: Final[tuple[float, float]] = (-40.0, 40.0)
#: 速率指令的自失效时长。超过这个时间没有新指令刷新，网关把云台速度归零。
#: 没有它，mission 崩溃时云台会一直转到限位。
PTZ_RATE_TTL_MS: Final[tuple[int, int]] = (100, 500)
TIMEOUT_MS: Final[tuple[int, int]] = (1, 30000)
#: 制动时延的验收指标（方案书 §9.3 / ICD §5.8）。
#:
#: **这是逻辑判据，不是 Schema 约束**（D3 决议 D1）。Schema 上限原来焊死在 100 ms，
#: 结果真机制动慢到 150 ms 时，收到的不是"一条记录着超标的报文"，而是一条解析失败
#: 的报文——恰好把最该留证的证据丢掉了，而那正是这个字段存在的唯一理由。
#: 现在 Schema 放宽到 5000 ms 只挡物理上不可能的量级，超标由网关在这里判。
BRAKE_LATENCY_LIMIT_MS: Final[int] = 100

# ---------------------------------------------------------------- 枚举
PAUSE_REASONS: Final[frozenset[str]] = frozenset({
    "VERIFY_REQUEST", "CLOUD_MANUAL", "WATCHDOG_RECOVER",
})
PTZ_SPEEDS: Final[frozenset[str]] = frozenset({"SLOW", "NORMAL"})
ISSUED_BY: Final[frozenset[str]] = frozenset({
    "MISSION_FSM", "CLOUD_MANUAL", "WATCHDOG",
})
MISSION_STATES: Final[tuple[str, ...]] = (
    "CRUISE", "SUSPECT", "HALT_REQ", "AIM", "ZOOM", "CAPTURE",
    "VERIFY", "PACK", "RESUME", "ABORT",
)

# ---------------------------------------------------------------- 心跳看门狗
#: ICD §4.5。差异清单 C2：方案书写 500 ms（2 Hz），ICD 写 200 ms（5 Hz），
#: 取 ICD。理由是抗丢包——1500 ms 超时下 2 Hz 只要连丢 3 条就误触发看门狗，
#: 5 Hz 要连丢 7 条，而桩的 ack_drop_rate 是 2 %。
HEARTBEAT_PERIOD_MS: Final[int] = 200
HEARTBEAT_TIMEOUT_MS: Final[int] = 1500
#: 心跳恢复后连续 3 条正常才解除看门狗态。
HEARTBEAT_RECOVER_COUNT: Final[int] = 3
#: 看门狗介入时下发的指令。注意是 RESUME 不是 PAUSE：AI 崩了的时候，车停在
#: 配电室通道中间比走完路线回充电位更麻烦（ICD §4.5）。
WATCHDOG_ACTION: Final[str] = "RESUME"

# ---------------------------------------------------------------- 拒绝码
REJECT_CODES: Final[tuple[str, ...]] = (
    "NOT_IN_WHITELIST", "SCHEMA_INVALID", "SCHEMA_VERSION_MISMATCH",
    "PARAM_MISSING", "PARAM_OUT_OF_RANGE", "UNKNOWN_WAYPOINT",
    "STATE_CONFLICT", "SAFETY_OVERRIDE", "HEARTBEAT_LOST",
    "DRIVER_NOT_READY", "DRIVER_TIMEOUT", "ESTOP_ACTIVE",
)
#: ICD 附录 A：只有这两个允许重试，且只重试一次。被拒绝的指令反复重试是最
#: 容易写出来的死循环，接口层面直接禁掉。
RETRYABLE: Final[frozenset[str]] = frozenset({"STATE_CONFLICT", "DRIVER_TIMEOUT"})


def in_range(value: float, bounds: tuple[float, float]) -> bool:
    """闭区间判定。越界一律拒绝，**不做截断**。

    ICD §4.3：截断会让 AI 侧的 bug 静默通过——发了 5 m 的 CREEP_FORWARD 被
    截成 0.5 m 照常执行，联调时看不出问题，等到某次截断逻辑失效就出事。
    """
    lo, hi = bounds
    return lo <= float(value) <= hi


#: 供 tools/validate.py 做第 8 项交叉比对：常量 → Schema 里的字段路径。
SCHEMA_CROSSCHECK: Final[tuple[tuple[str, tuple, str, str], ...]] = (
    ("CREEP_DISTANCE_M", CREEP_DISTANCE_M,
     "control_command.schema.json", "$defs.creepPar.properties.distance_m"),
    ("GOTO_TOLERANCE_M", GOTO_TOLERANCE_M,
     "control_command.schema.json", "$defs.gotoPar.properties.tolerance_m"),
    ("PTZ_PAN_DEG", PTZ_PAN_DEG,
     "control_command.schema.json", "$defs.ptzPar.properties.pan_deg"),
    ("PTZ_TILT_DEG", PTZ_TILT_DEG,
     "control_command.schema.json", "$defs.ptzPar.properties.tilt_deg"),
    ("PTZ_ZOOM", PTZ_ZOOM,
     "control_command.schema.json", "$defs.ptzPar.properties.zoom"),
    ("PTZ_PAN_DPS", PTZ_PAN_DPS,
     "control_command.schema.json", "$defs.ratePar.properties.pan_dps"),
    ("PTZ_TILT_DPS", PTZ_TILT_DPS,
     "control_command.schema.json", "$defs.ratePar.properties.tilt_dps"),
    ("PTZ_RATE_TTL_MS", PTZ_RATE_TTL_MS,
     "control_command.schema.json", "$defs.ratePar.properties.ttl_ms"),
    ("TIMEOUT_MS", TIMEOUT_MS,
     "control_command.schema.json", "properties.timeout_ms"),
    ("PTZ_PAN_DEG", PTZ_PAN_DEG,
     "status_report.schema.json", "properties.ptz.properties.pan_deg"),
    ("PTZ_TILT_DEG", PTZ_TILT_DEG,
     "status_report.schema.json", "properties.ptz.properties.tilt_deg"),
    ("PTZ_ZOOM", PTZ_ZOOM,
     "status_report.schema.json", "properties.ptz.properties.zoom"),
)
