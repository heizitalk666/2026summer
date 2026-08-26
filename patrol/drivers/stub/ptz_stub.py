"""云台桩。ICD §9.2。

**这个桩是 PID 伺服的被控对象。**如果它只是把 pan 直接赋值成目标值，那么
mission/servo.py 整定出来的参数、导出的阶跃响应曲线、超调量与调节时间全都
是假的，答辩时一问就穿。所以这里实现了真实的二阶特性：

- 角速度上限（pan 60 °/s、tilt 40 °/s）——决定 AIM 耗时，也是限幅的来源
- 角加速度上限 240 °/s²——机械惯性，PID 微分项要对付的东西
- 齿隙 backlash_deg——换向时的死区，表现为"缓慢逼近不到位"（方案书 §9.4）
- 到位抖动 settle_jitter_deg——at_target 置位后仍有残余抖动，
  这正是 CAPTURE 要连拍 3 帧的理由
- 变焦耗时 800–1400 ms + 对焦耗时，5 % 概率对焦失败

有效感光像素比 k = min(1, 2/z) 不在这里实现，而在 scene/render.py：桩用
降分辨率渲染再上采样来制造真实的信息损失，比在这里乘个系数诚实。
"""
from __future__ import annotations

import math
import threading

import numpy as np

from patrol.common.clock import mono_ns
from patrol.drivers.base import (ExecHandle, ExecProgress, ExecResult, FocusState,
                                 IPTZ, ParamOutOfRange, PTZCaps, PTZSpeed, PTZStatus)
from patrol.scene.optics import hfov_at_zoom

_TICK_HZ = 100.0
_AT_TARGET_DEG = 0.25          # 到位判据
_SPEED_SCALE = {PTZSpeed.SLOW: 0.35, PTZSpeed.NORMAL: 1.0}


class _Axis:
    """单轴：位置、速度、限幅、齿隙。"""

    __slots__ = ("pos", "vel", "target", "vmax", "amax", "backlash",
                 "lo", "hi", "_last_dir", "_lash", "rate_cmd", "rate_until_ns")

    def __init__(self, lo: float, hi: float, vmax: float, amax: float,
                 backlash: float):
        self.pos = 0.0
        self.vel = 0.0
        self.target: float | None = 0.0
        self.vmax, self.amax, self.backlash = vmax, amax, backlash
        self.lo, self.hi = lo, hi
        self._last_dir = 0.0
        self._lash = 0.0
        self.rate_cmd: float | None = None
        self.rate_until_ns = 0

    def step(self, dt: float, now_ns: int, speed_scale: float = 1.0) -> None:
        vmax = self.vmax * speed_scale
        # 速率模式优先，且到期自动归零（ttl 是这条通路的安全兜底）
        if self.rate_cmd is not None:
            if now_ns >= self.rate_until_ns:
                self.rate_cmd = None
                want_v = 0.0
            else:
                want_v = float(np.clip(self.rate_cmd, -vmax, vmax))
        elif self.target is not None:
            err = self.target - self.pos
            # 梯形速度规划：够不够刹车距离决定加速还是减速
            brake = self.vel * self.vel / (2.0 * max(1e-6, self.amax))
            if abs(err) <= brake:
                want_v = 0.0
            else:
                want_v = float(np.clip(math.copysign(vmax, err), -vmax, vmax))
        else:
            want_v = 0.0

        dv = float(np.clip(want_v - self.vel, -self.amax * dt, self.amax * dt))
        self.vel += dv

        # 齿隙：换向时先要吃掉空程，表现为"发了指令但一开始不动"
        d = math.copysign(1.0, self.vel) if abs(self.vel) > 1e-6 else 0.0
        if d != 0.0 and self._last_dir != 0.0 and d != self._last_dir:
            self._lash = self.backlash
        if d != 0.0:
            self._last_dir = d
        move = self.vel * dt
        if self._lash > 0.0:
            eat = min(self._lash, abs(move))
            self._lash -= eat
            move = math.copysign(max(0.0, abs(move) - eat), move) if move else 0.0

        self.pos = float(np.clip(self.pos + move, self.lo, self.hi))
        if self.pos in (self.lo, self.hi) and abs(self.vel) > 0:
            if (self.pos == self.lo and self.vel < 0) or (self.pos == self.hi and self.vel > 0):
                self.vel = 0.0

    def at_target(self) -> bool:
        return (self.rate_cmd is None and self.target is not None
                and abs(self.target - self.pos) <= _AT_TARGET_DEG
                and abs(self.vel) <= 1.0)


class _Job:
    __slots__ = ("kind", "handle", "t0_ns", "progress", "fail_reason")

    def __init__(self, kind: str, handle: ExecHandle):
        self.kind, self.handle = kind, handle
        self.t0_ns = handle.issued_ts_mono_ns
        self.progress = ExecProgress.IN_PROGRESS
        self.fail_reason: str | None = None


class PTZStub(IPTZ):
    def __init__(self, cfg, seed: int = 0):
        c = cfg.get("stub.ptz")
        self.rng = np.random.default_rng(seed)
        self._hfov_1x = float(cfg.get("optics.hfov_at_1x_deg", 60.0))
        self._jitter = float(c.get("settle_jitter_deg", 0.15))
        self._zoom_ms = tuple(c.get("zoom_time_ms", [800, 1400]))
        self._focus_ms = tuple(c.get("focus_time_ms", [300, 700]))
        self._focus_fail = float(c.get("focus_fail_rate", 0.05))

        self._caps = PTZCaps(
            pan_range_deg=(-170.0, 170.0),
            tilt_range_deg=(-30.0, 60.0),
            max_zoom=float(c.get("max_zoom", 3.0)),
            hfov_at_1x_deg=self._hfov_1x,
            zoom_is_optical=bool(c.get("zoom_is_optical", True)),
            max_pan_dps=float(c.get("pan_speed_dps", 60.0)),
            max_tilt_dps=float(c.get("tilt_speed_dps", 40.0)),
        )
        accel = float(c.get("accel_dps2", 240.0))
        lash = float(c.get("backlash_deg", 0.05))
        self._pan = _Axis(-170.0, 170.0, self._caps.max_pan_dps, accel, lash)
        self._tilt = _Axis(-30.0, 60.0, self._caps.max_tilt_dps, accel, lash)

        self._lock = threading.RLock()
        self._zoom = 1.0
        self._zoom_target = 1.0
        self._zoom_done_ns = 0
        self._focus = FocusState.LOCKED
        self._focus_done_ns = 0
        self._speed_scale = 1.0
        self._jobs: dict[str, _Job] = {}
        self._seq = 0
        self._jit_pan = 0.0
        self._jit_tilt = 0.0

        self._stop = threading.Event()
        self._thr = threading.Thread(target=self._loop, name="ptz_stub", daemon=True)
        self._thr.start()

    # ------------------------------------------------------------ 内部
    def _loop(self) -> None:
        dt = 1.0 / _TICK_HZ
        while not self._stop.wait(dt):
            with self._lock:
                self._tick(dt)

    def _tick(self, dt: float) -> None:
        now = mono_ns()
        self._pan.step(dt, now, self._speed_scale)
        self._tilt.step(dt, now, self._speed_scale)

        # 变焦：机构慢，到位后重新对焦
        if self._zoom_done_ns and now >= self._zoom_done_ns:
            self._zoom = self._zoom_target
            self._zoom_done_ns = 0
            if self.rng.random() < self._focus_fail:
                self._focus = FocusState.FAILED
                self._focus_done_ns = 0
            else:
                self._focus = FocusState.FOCUSING
                self._focus_done_ns = now + int(self.rng.integers(
                    int(self._focus_ms[0]), int(self._focus_ms[1]) + 1)) * 1_000_000
        elif self._zoom_done_ns:
            # 变焦过程中焦距连续变化，画面在动
            span = self._zoom_target - self._zoom
            self._zoom += span * min(1.0, dt * 3.0)

        if self._focus_done_ns and now >= self._focus_done_ns:
            self._focus = FocusState.LOCKED
            self._focus_done_ns = 0

        # 到位抖动：at_target 置位后仍有残余抖动。CAPTURE 连拍 3 帧就是为它。
        settled = self._pan.at_target() and self._tilt.at_target()
        if settled and self._jitter > 0:
            self._jit_pan = float(self.rng.normal(0.0, self._jitter * 0.5))
            self._jit_tilt = float(self.rng.normal(0.0, self._jitter * 0.5))
        else:
            self._jit_pan = self._jit_tilt = 0.0

        done = settled and self._zoom_done_ns == 0 and self._focus is not FocusState.FOCUSING
        for j in self._jobs.values():
            if j.progress is not ExecProgress.IN_PROGRESS:
                continue
            if j.kind == "SET_RATE":
                if self._pan.rate_cmd is None and self._tilt.rate_cmd is None:
                    j.progress = ExecProgress.DONE
            elif done:
                if self._focus is FocusState.FAILED:
                    j.progress = ExecProgress.FAILED
                    j.fail_reason = "FOCUS_FAILED"
                else:
                    j.progress = ExecProgress.DONE

    def _new_handle(self, kind: str) -> tuple[ExecHandle, _Job]:
        self._seq += 1
        h = ExecHandle(f"ptz-{kind.lower()}-{self._seq:04x}", mono_ns())
        j = _Job(kind, h)
        self._jobs[h.handle_id] = j
        return h, j

    # ------------------------------------------------------------ IPTZ
    def capabilities(self) -> PTZCaps:
        return self._caps

    def set_pose(self, pan_deg: float, tilt_deg: float, zoom: float,
                 speed: PTZSpeed) -> ExecHandle:
        lo, hi = self._caps.pan_range_deg
        if not (lo - 1e-9 <= pan_deg <= hi + 1e-9):
            raise ParamOutOfRange("pan %.2f 超出云台机械范围 [%.1f, %.1f]" % (pan_deg, lo, hi))
        lo, hi = self._caps.tilt_range_deg
        if not (lo - 1e-9 <= tilt_deg <= hi + 1e-9):
            raise ParamOutOfRange("tilt %.2f 超出云台机械范围 [%.1f, %.1f]" % (tilt_deg, lo, hi))
        if not (1.0 - 1e-9 <= zoom <= self._caps.max_zoom + 1e-9):
            raise ParamOutOfRange("zoom %.2f 超出 [1.0, %.1f]" % (zoom, self._caps.max_zoom))

        with self._lock:
            h, _ = self._new_handle("SET_POSE")
            self._pan.rate_cmd = self._tilt.rate_cmd = None
            self._pan.target = float(pan_deg)
            self._tilt.target = float(tilt_deg)
            self._speed_scale = _SPEED_SCALE.get(speed, 1.0)
            if abs(float(zoom) - self._zoom_target) > 1e-3:
                self._zoom_target = float(zoom)
                self._zoom_done_ns = mono_ns() + int(self.rng.integers(
                    int(self._zoom_ms[0]), int(self._zoom_ms[1]) + 1)) * 1_000_000
                self._focus = FocusState.FOCUSING
        return h

    def set_rate(self, pan_dps: float, tilt_dps: float, ttl_ms: int) -> ExecHandle:
        """差异清单 A1 的速率通路。PID 伺服走这条。

        ttl_ms 到期自动归零——没有它，mission 崩溃时云台会一直转到限位。
        """
        if abs(pan_dps) > self._caps.max_pan_dps + 1e-9:
            raise ParamOutOfRange("pan_dps %.1f 超出 ±%.1f" % (pan_dps, self._caps.max_pan_dps))
        if abs(tilt_dps) > self._caps.max_tilt_dps + 1e-9:
            raise ParamOutOfRange("tilt_dps %.1f 超出 ±%.1f" % (tilt_dps, self._caps.max_tilt_dps))
        with self._lock:
            h, _ = self._new_handle("SET_RATE")
            now = mono_ns()
            until = now + int(max(1, ttl_ms)) * 1_000_000
            self._pan.rate_cmd, self._pan.rate_until_ns = float(pan_dps), until
            self._tilt.rate_cmd, self._tilt.rate_until_ns = float(tilt_dps), until
            self._pan.target = self._tilt.target = None
            self._speed_scale = 1.0
        return h

    def home(self) -> ExecHandle:
        return self.set_pose(0.0, 0.0, 1.0, PTZSpeed.NORMAL)

    def status(self) -> PTZStatus:
        with self._lock:
            z = float(self._zoom)
            moving = (abs(self._pan.vel) > 0.5 or abs(self._tilt.vel) > 0.5
                      or self._zoom_done_ns != 0)
            return PTZStatus(
                pan_deg=round(self._pan.pos + self._jit_pan, 4),
                tilt_deg=round(self._tilt.pos + self._jit_tilt, 4),
                zoom=round(z, 4),
                hfov_deg=round(hfov_at_zoom(self._hfov_1x, z), 4),
                moving=bool(moving),
                focus_state=self._focus,
                at_target=bool(self._pan.at_target() and self._tilt.at_target()
                               and self._zoom_done_ns == 0),
                ts_mono_ns=mono_ns(),
            )

    def poll(self, handle: ExecHandle) -> ExecResult:
        with self._lock:
            j = self._jobs.get(handle.handle_id)
        if j is None:
            return ExecResult(ExecProgress.FAILED, 0, "未知句柄")
        return ExecResult(j.progress, int((mono_ns() - j.t0_ns) // 1_000_000),
                          j.fail_reason)

    def close(self) -> None:
        self._stop.set()
        self._thr.join(timeout=1.0)

    # ------------------------------------------------------------ 测试用
    def true_pose(self) -> tuple[float, float, float]:
        """不含抖动的真实位姿，仅供渲染与测试；status() 才是驱动对外的口径。"""
        with self._lock:
            return self._pan.pos + self._jit_pan, self._tilt.pos + self._jit_tilt, self._zoom
