"""云台视觉伺服 PID。方案书 §6.3.1–§6.3.4。

**这是本课题控制部分的核心，也是差异清单 A1 争的那条通路。**

控制目标是让被测目标的形心与画面中心重合。方位与俯仰两个通道结构相同、
互不耦合，各跑一个 PID。

    给定值  画面中心横坐标 W/2
    反馈值  目标形心横坐标 x（来自 DetectionEvent.detections[].bbox）
    偏差    e = W/2 − x，单位像素
    控制量  云台方位角速度 ω，单位度每秒

位置式 PID 的离散形式：

    u(k) = Kp·e(k) + Ki·T·Σe(j) + Kd·[e(k) − e(k−1)]/T

三个必须处理的问题：

**一、变焦引起的对象传递系数变化（§6.3.2）。**像素偏差与实际角度偏差的
换算关系随变焦倍率变化：Δφ = e·θ/(W·z)。变焦到 3× 时同样的像素偏差只对应
三分之一的实际角度偏差；PID 参数不变的话控制量会是所需值的 3 倍，必然超调
甚至持续振荡。所以要做**增益调度**，把输出按倍率折算：

    ω(k) = [θ/(W·z)]·u(k)

这属于"对象参数随工况变化"，增益调度使等效开环增益保持恒定，是处理这类
问题的常规做法。**关掉调度可以现场演示变焦后的振荡作为对比**——
`gain_schedule: false` 就是给答辩留的这个开关。

**二、抗积分饱和（§6.3.4）。**云台角速度有物理上限。偏差大时输出被限幅而
积分项仍在累积，偏差反向后要很久才退出饱和，表现为大幅超调。采用积分分离：
|e| 超过阈值时暂停积分累积，只投比例与微分；回到阈值内再恢复。

**三、微分项对噪声敏感。**目标形心的像素坐标有帧间抖动，直接送 PID 会引起
控制量高频振荡。反馈量先过一阶惯性滤波（§6.1.3），α 取 0.6。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class PIDGains:
    kp: float = 0.40
    ki: float = 0.05
    kd: float = 0.02


@dataclass
class ServoSample:
    """一次控制周期的完整记录，用于导出阶跃响应曲线。"""

    t_s: float
    error_px: float
    error_filtered_px: float
    p_term: float
    i_term: float
    d_term: float
    u: float
    omega_dps: float
    saturated: bool
    integral_frozen: bool
    zoom: float


class AxisPID:
    """单轴 PID：增益调度 + 积分分离 + 积分限幅 + 输出限幅。"""

    def __init__(self, gains: PIDGains, *, period_s: float, omega_max_dps: float,
                 hfov_at_1x_deg: float, image_span_px: int,
                 integral_separation_px: float = 150.0,
                 integral_max: float = 500.0, alpha: float = 0.6,
                 gain_schedule: bool = True):
        self.g = gains
        self.T = float(period_s)
        self.omega_max = float(omega_max_dps)
        self.theta = float(hfov_at_1x_deg)
        self.W = int(image_span_px)
        self.e_th = float(integral_separation_px)
        self.i_max = float(integral_max)
        self.alpha = float(alpha)
        self.gain_schedule = bool(gain_schedule)
        self.reset()

    def reset(self) -> None:
        self._integral = 0.0
        self._e_prev = 0.0
        self._e_filt = 0.0
        self._primed = False
        self.history: list[ServoSample] = []

    # ------------------------------------------------------------
    def step(self, error_px: float, *, zoom: float = 1.0,
             t_s: float | None = None, record: bool = True) -> float:
        """一个控制周期。返回云台角速度指令，度每秒。"""
        e = float(error_px)
        # 一阶惯性滤波（指数移动平均）：形心像素坐标有帧间抖动，
        # 直接送 PID 会让微分项高频振荡。α 越小滤波越强但相位滞后越大。
        if not self._primed:
            self._e_filt = e
            self._e_prev = e
            self._primed = True
        else:
            self._e_filt = self.alpha * e + (1.0 - self.alpha) * self._e_filt
        ef = self._e_filt

        # 积分分离：偏差大时暂停积分累积，避免限幅期间积分持续膨胀
        frozen = abs(ef) > self.e_th
        if not frozen:
            self._integral += ef * self.T
            self._integral = float(np.clip(self._integral, -self.i_max, self.i_max))

        p = self.g.kp * ef
        i = self.g.ki * self._integral
        d = self.g.kd * (ef - self._e_prev) / max(1e-9, self.T)
        self._e_prev = ef
        u = p + i + d

        # 增益调度：把像素域的控制量折算成角速度。z 越大，同样的像素偏差
        # 对应的角度偏差越小，折算系数随之变小，等效开环增益保持恒定。
        if self.gain_schedule:
            k = self.theta / (self.W * max(1e-6, float(zoom)))
        else:
            k = self.theta / self.W          # 不调度：按广角端折算，变焦后会振荡
        omega_raw = k * u
        omega = float(np.clip(omega_raw, -self.omega_max, self.omega_max))
        sat = abs(omega_raw) > self.omega_max + 1e-9

        if record:
            self.history.append(ServoSample(
                t_s=float(t_s if t_s is not None else len(self.history) * self.T),
                error_px=e, error_filtered_px=ef, p_term=p, i_term=i, d_term=d,
                u=u, omega_dps=omega, saturated=sat, integral_frozen=frozen,
                zoom=float(zoom)))
        return omega

    # ------------------------------------------------------------ 指标
    def metrics(self, *, deadband_px: float = 20.0) -> dict:
        """从记录里算出三项动态指标（方案书 §2.3 / §9.3）。

        超调量 ≤10 %、调节时间 ≤1.5 s、稳态误差 ≤20 px。
        """
        if len(self.history) < 3:
            return {}
        e = np.array([s.error_px for s in self.history], float)
        t = np.array([s.t_s for s in self.history], float)
        e0 = float(e[0])
        if abs(e0) < 1e-6:
            return {}
        # 超调：偏差越过零点后反向的最大幅度，相对初始阶跃
        crossed = np.sign(e) != np.sign(e0)
        overshoot = float(np.max(np.abs(e[crossed])) / abs(e0) * 100.0) if np.any(crossed) else 0.0
        # 调节时间：偏差进入 ±deadband 带且此后不再越出
        inside = np.abs(e) <= deadband_px
        settle = None
        for i in range(len(e)):
            if inside[i] and bool(np.all(inside[i:])):
                settle = float(t[i] - t[0])
                break
        # 稳态误差要在**进入死区之后**那段上量。固定取最后 10 拍的话，一次
        # 只跑了 12 拍就收敛的整定过程会把开头的大偏差算进来——实测报出过
        # settling_time = 0.9 s 而 steady_error = 195 px 这种自相矛盾的组合，
        # 读日志的人会以为伺服没收敛。
        if settle is not None:
            i0 = int(np.searchsorted(t - t[0], settle))
            tail = e[i0:] if len(e) - i0 >= 3 else e[max(0, len(e) - 3):]
        else:
            tail = e[max(0, len(e) - 10):]
        return {
            "step_px": e0,
            "overshoot_pct": round(overshoot, 3),
            "settling_time_s": None if settle is None else round(settle, 3),
            "steady_error_px": round(float(np.mean(np.abs(tail))), 3),
            "saturated_frac": round(float(np.mean([s.saturated for s in self.history])), 3),
            "integral_frozen_frac": round(
                float(np.mean([s.integral_frozen for s in self.history])), 3),
            "samples": len(self.history),
        }

    def meets_spec(self, *, deadband_px: float = 20.0) -> bool:
        m = self.metrics(deadband_px=deadband_px)
        if not m:
            return False
        return (m["overshoot_pct"] <= 10.0
                and m["settling_time_s"] is not None and m["settling_time_s"] <= 1.5
                and m["steady_error_px"] <= deadband_px)


class GimbalServo:
    """方位 + 俯仰双通道。两通道结构相同、互不耦合。"""

    def __init__(self, cfg, caps=None):
        s = cfg.get("mission.servo")
        g = PIDGains(kp=float(s.get("kp", 0.40)), ki=float(s.get("ki", 0.05)),
                     kd=float(s.get("kd", 0.02)))
        self.period_s = float(s.get("period_ms", 100)) / 1000.0
        self.deadband_px = float(s.get("deadband_px", 20.0))
        self.rate_ttl_ms = int(s.get("rate_ttl_ms", 300))
        self.mode = str(s.get("mode", "pid")).lower()
        theta = float(cfg.get("optics.hfov_at_1x_deg", 60.0))
        W = int(cfg.get("camera.width", 1920))
        H = int(cfg.get("camera.height", 1080))
        pan_max = float(getattr(caps, "max_pan_dps", 0.0) or 60.0)
        tilt_max = float(getattr(caps, "max_tilt_dps", 0.0) or 40.0)

        common = dict(period_s=self.period_s,
                      integral_separation_px=float(s.get("integral_separation_px", 150.0)),
                      integral_max=float(s.get("integral_max", 500.0)),
                      alpha=float(s.get("alpha", 0.6)),
                      gain_schedule=bool(s.get("gain_schedule", True)))
        self.pan = AxisPID(g, omega_max_dps=pan_max, hfov_at_1x_deg=theta,
                           image_span_px=W, **common)
        # 俯仰通道的"视场角"要用垂直视场角，否则折算系数差一个画幅比
        self.tilt = AxisPID(g, omega_max_dps=tilt_max,
                            hfov_at_1x_deg=theta * H / max(1, W),
                            image_span_px=H, **common)
        self.W, self.H = W, H
        #: 连续多少拍在死区内才算到位。3 拍 = 300 ms，够滤掉形心抖动。
        self.settle_ticks = int(s.get("settle_ticks", 3))
        self._in_band = 0

    def reset(self) -> None:
        self.pan.reset()
        self.tilt.reset()
        self._in_band = 0

    def step(self, cx: float, cy: float, *, zoom: float,
             t_s: float | None = None) -> tuple[float, float]:
        """给定目标形心像素坐标，返回 (pan_dps, tilt_dps)。

        **两个通道的符号都是负反馈，谁都不能多一个负号。**

        方位：目标在画面右侧（cx > W/2）时 e < 0，输出为负，pan 减小。相机
        方位角 = yaw + pan 且 map 系逆时针为正，pan 减小即向右转，目标回中。

        俯仰：目标在画面上方（cy < H/2）时 e > 0，输出为正，tilt 增大。
        tilt 正方向是抬头（PinholeCamera 里 fwd.z = sin(tilt)），抬头正好把
        上方的目标带回中心。**这里曾经多写了一个负号**，两条通道就成了一正
        一负——方位收敛得好好的，俯仰却在发散：初始 2° 的俯仰偏差（巡航
        tilt=2.0° 而表计与相机等高）被一路放大，约 1 s 后目标垂直出框，
        随后 AIM 只能干等到超时 ABORT。整条链路上唯一的症状是"目标突然
        消失"，很容易误判成检测器漏检或者渲染出错。
        """
        ex = self.W / 2.0 - float(cx)
        ey = self.H / 2.0 - float(cy)
        pan = self.pan.step(ex, zoom=zoom, t_s=t_s)
        tilt = self.tilt.step(ey, zoom=zoom, t_s=t_s)
        return pan, tilt

    def on_target(self) -> bool:
        """偏差进入死区且**连续保持**若干拍才算到位。

        两条都不能少：
        - 没喂过样本时 _e_filt 是 0，直接判"到位"会让 AIM 状态一拍就过，
          PID 根本没跑过——实测审计日志里一条 PTZ_RATE 都没有就是这个原因。
        - 只看单拍会被形心抖动骗过去，云台还在动就进 ZOOM，变焦后目标出框。
        """
        if not (self.pan._primed and self.tilt._primed):
            return False
        inside = (abs(self.pan._e_filt) <= self.deadband_px
                  and abs(self.tilt._e_filt) <= self.deadband_px)
        self._in_band = (self._in_band + 1) if inside else 0
        return self._in_band >= self.settle_ticks

    def metrics(self) -> dict:
        return {"pan": self.pan.metrics(deadband_px=self.deadband_px),
                "tilt": self.tilt.metrics(deadband_px=self.deadband_px)}
