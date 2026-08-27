"""驱动层抽象接口。ICD §8。

网关之下、硬件之上的一层。它存在的唯一理由是让桩和真机可以互换：
gateway 只依赖这四个抽象基类，启动时按配置注入桩实现或真机实现，
网关代码一行不改。

三条约定（ICD §8.1）：

**一、所有会改变物理状态的方法都是非阻塞的。**它们立即返回一个 ExecHandle，
调用方通过 poll(handle) 查询进度。阻塞式的 goto_and_wait() 看起来省事，但
状态机的每状态超时会失效——超时逻辑在状态机里，阻塞在驱动里，两者管不到对方。

**二、驱动层不承担任务级安全校验。**distance_m ≤ 0.5 这类约束在网关。驱动层
只按硬件能力校验（云台转不到 200°），越界抛 ParamOutOfRange，不截断。两层
校验的依据不同：网关的依据是任务需求，驱动的依据是硬件手册。

**三、capabilities() 是开机自检的依据。**系统启动时 mission 读取各驱动的能力
声明，与任务需求对照，不满足则拒绝启动并打印差在哪。这条把像素密度判据从
纸面结论变成了运行时检查：PTZCaps.max_zoom < 3.0 时系统直接不启动，而不是
等到现场发现表读不出来。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import numpy as np

from patrol.common.errors import (CapabilityError, DriverError, DriverNotReady,
                                  DriverTimeout, ParamOutOfRange)

__all__ = [
    "ExecProgress", "ExecHandle", "ExecResult",
    "ChassisState", "ChassisStatus", "ChassisCaps", "IChassis",
    "PTZSpeed", "FocusState", "PTZStatus", "PTZCaps", "IPTZ",
    "Frame", "CameraCaps", "ICamera",
    "PoseSource", "Pose", "ILocalizer",
    "DriverError", "DriverNotReady", "ParamOutOfRange", "DriverTimeout",
    "CapabilityError", "selftest",
]


# ---------------------------------------------------------------- 公共类型
class ExecProgress(Enum):
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    FAILED = "FAILED"
    PREEMPTED = "PREEMPTED"


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


# ---------------------------------------------------------------- IChassis
class ChassisState(Enum):
    MOVING = "MOVING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    PAUSED = "PAUSED"
    RETURNING = "RETURNING"
    FAULT = "FAULT"
    ESTOP = "ESTOP"


@dataclass
class ChassisStatus:
    state: ChassisState
    speed_mps: float
    path_progress: float                 # 0–1
    distance_to_goal_m: Optional[float]
    current_waypoint_id: Optional[str]
    battery_pct: float
    safety_layer_active: bool
    ts_mono_ns: int


@dataclass
class ChassisCaps:
    supports_task_level: bool            # 是否支持 GOTO_OBSERVE 这类任务级指令
    max_speed_mps: float
    max_creep_m: float                   # 硬件允许的单次微动上限
    has_safety_layer: bool               # 是否具备独立于上层的安全层
    waypoint_ids: list[str] = field(default_factory=list)


class IChassis(ABC):
    """底盘驱动。实现方：硬件组（真机）/ 软件组（chassis_stub）。

    supports_task_level 为 False 时，mission 拒绝启动。速度级接口无法在不
    引入转向控制的前提下实现 GOTO_OBSERVE，而转向控制不在协议白名单内。
    """

    @abstractmethod
    def capabilities(self) -> ChassisCaps: ...

    @abstractmethod
    def pause(self, reason: str) -> ExecHandle:
        """请求停车。完成判据是 status().state == STOPPED，不是本函数返回。"""

    @abstractmethod
    def resume(self) -> ExecHandle:
        """恢复原巡检路线。从任何非 ESTOP 状态调用都必须被接受。

        这条是给看门狗用的：AI 进程崩溃时网关下发 RESUME，此时底盘可能处于
        PAUSED、STOPPING、FAULT 中的任意一个，如果驱动因为状态不对而拒绝，
        车就真的卡在路上了。
        """

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
        """注册安全事件回调。回调在驱动内部线程触发，实现方必须保证从事件
        发生到回调被调用不超过 20 ms。回调内不得阻塞。"""

    @abstractmethod
    def close(self) -> None: ...


# ---------------------------------------------------------------- IPTZ
class PTZSpeed(Enum):
    SLOW = "SLOW"
    NORMAL = "NORMAL"


class FocusState(Enum):
    FOCUSING = "FOCUSING"
    LOCKED = "LOCKED"
    FAILED = "FAILED"


@dataclass
class PTZStatus:
    pan_deg: float
    tilt_deg: float
    zoom: float
    hfov_deg: float                      # 当前倍率下的实际水平视场角
    moving: bool
    focus_state: FocusState
    at_target: bool
    ts_mono_ns: int


@dataclass
class PTZCaps:
    pan_range_deg: tuple[float, float]
    tilt_range_deg: tuple[float, float]
    max_zoom: float                      # 光学变焦，不含数字变焦
    hfov_at_1x_deg: float                # 广角端水平视场角，像素密度公式的 θ
    zoom_is_optical: bool
    #: 差异清单 A1 增补：速率闭环所需的能力声明。不支持速率的云台此处为 0，
    #: mission 会自动退回 servo.mode=open_loop 而不是崩掉。
    max_pan_dps: float = 0.0
    max_tilt_dps: float = 0.0


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

    # -- 差异清单 A1 增补的速率通路 --------------------------------
    def set_rate(self, pan_dps: float, tilt_dps: float,
                 ttl_ms: int) -> ExecHandle:
        """速率闭环。ttl_ms 内没有新指令刷新则自动归零。

        默认实现抛 NotImplementedError——不支持速率的云台通过 capabilities()
        里的 max_pan_dps=0 声明这一点，mission 据此退回开环模式。

        ttl_ms 是这条指令的安全兜底：没有它，mission 崩溃时云台会一直转到
        限位。归零由**网关**执行（网关本来就有看门狗计时），不依赖 AI 侧。
        """
        raise NotImplementedError("本云台不支持速率控制")


# ---------------------------------------------------------------- ICamera
@dataclass
class Frame:
    seq: int
    ts_mono_ns: int                      # 曝光开始时刻，不是取回时刻
    ts_utc_ms: int
    image: np.ndarray                    # HxWx3, BGR, uint8
    width: int
    height: int


@dataclass
class CameraCaps:
    width: int
    height: int
    max_fps: int
    pixel_format: str                    # "BGR888"


class ICamera(ABC):
    """相机驱动。真机走 RK3576 的 MPP 硬解，桩走场景渲染。"""

    @abstractmethod
    def capabilities(self) -> CameraCaps: ...

    @abstractmethod
    def start(self, width: int, height: int, fps: int) -> None: ...

    @abstractmethod
    def grab(self, timeout_ms: int = 200) -> Frame:
        """取一帧最新图像。超时抛 DriverTimeout。

        Frame.ts_mono_ns 取曝光开始时刻。取回时刻会把解码延迟算进去，而
        DetectionEvent.latency_ms.capture_to_infer 要观测的正是这段延迟，
        用取回时刻会让它恒等于零，观测点就废了。
        """

    @abstractmethod
    def grab_burst(self, n: int, interval_ms: int) -> list[Frame]:
        """连拍 n 帧。CAPTURE 状态用 n=3, interval_ms=150。
        必须保证 n 帧之间没有丢帧，不足 n 帧抛 DriverError。"""

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    def observe_state(self, *, pose_xy_yaw: tuple[float, float, float],
                      pan_deg: float, tilt_deg: float, zoom: float,
                      speed_mps: float) -> None:
        """告知相机"这台机器此刻的真实视点"。**真机实现为空。**

        真相机的画面本来就来自真实世界，不需要谁来告诉它云台转到哪了。桩不然：
        桩要扮演的那个物理世界住在**网关进程**里（只有网关能碰执行器，ICD §1.1），
        而感知进程里的 chassis/ptz 一条指令都收不到，永远停在开机位。不喂这一
        步，感知渲染出来的画面和 IF-3 报的状态说的是两个世界——实测表现为复核
        期间 AIM 三十拍一个检出都没有。

        视点数据全部取自 IF-3 StatusReport，**不新增任何接口**：
        ``pose.{x_m,y_m,yaw_deg}`` / ``ptz.{pan_deg,tilt_deg,zoom}`` /
        ``chassis.speed_mps``。PTZStub.status() 与 true_pose() 返回的是同一个
        含抖动的值，所以改走 IF-3 一点保真度都不损失。
        """
        return None


# ---------------------------------------------------------------- ILocalizer
class PoseSource(Enum):
    LIDAR_SLAM = "LIDAR_SLAM"
    ODOM_ONLY = "ODOM_ONLY"
    LOST = "LOST"


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


# ---------------------------------------------------------------- 开机自检
def selftest(chassis: IChassis, ptz: IPTZ, camera: ICamera,
             *, need_zoom: float = 3.0, need_pixel_density_px: float = 120.0,
             need_creep_m: float = 0.50) -> list[str]:
    """把纸面判据变成运行时检查。ICD §8.1 第三条。

    返回不满足项的说明列表，空列表表示通过。mission 启动时调用，非空则
    **拒绝启动并打印差在哪**，而不是等到现场发现表读不出来。
    """
    problems: list[str] = []

    cc = chassis.capabilities()
    if not cc.supports_task_level:
        problems.append(
            "底盘不支持任务级指令（supports_task_level=False）：速度级接口无法在"
            "不引入转向控制的前提下实现 GOTO_OBSERVE，而转向控制不在协议白名单内"
        )
    if not cc.has_safety_layer:
        problems.append(
            "底盘未声明独立安全层（has_safety_layer=False）：≤100 ms 的制动时限"
            "无法由 AI 侧保证，这条不能由网关兜底"
        )
    if cc.max_creep_m + 1e-9 < need_creep_m:
        problems.append("底盘 max_creep_m=%.2f m < 协议要求的 %.2f m"
                        % (cc.max_creep_m, need_creep_m))

    pc = ptz.capabilities()
    if pc.max_zoom + 1e-9 < need_zoom:
        problems.append(
            "云台 max_zoom=%.2f < %.1f：巡航 5 m 处表盘只有 49.9 px，"
            "达不到 %.0f px 的可靠读数下限，指针表无法自动读数"
            % (pc.max_zoom, need_zoom, need_pixel_density_px)
        )
    if not pc.zoom_is_optical:
        problems.append(
            "云台变焦非光学（zoom_is_optical=False）：数字变焦不增加感光像素，"
            "像素密度公式对它不成立"
        )

    mc = camera.capabilities()
    if mc.width < 1920 or mc.height < 1080:
        problems.append("相机分辨率 %dx%d 低于 1920x1080：像素密度判据按 W=1920 推导"
                        % (mc.width, mc.height))
    return problems
