"""底盘桩。ICD §9.1。

桩不是"能跑就行"的假实现。这里注入的每一项都对应真机上会出现的具体麻烦：

| 注入项            | 模拟的真实情况                    | 不注入会怎样                       |
|-------------------|-----------------------------------|------------------------------------|
| stop_delay_ms     | 减速到停稳需要时间且不确定        | HALT_REQ 的等待逻辑没被测过        |
| ack_drop_rate     | 串口/CAN 偶发丢包                 | 状态机在 M2"连续 3 次触发"上翻车   |
| safety_event      | 行人、临时堆放物                  | 复核中止路径是死代码               |
| ESTOP_PRESSED     | 唯一不能自恢复的安全事件          | 真机上第一次按急停就是现场事故     |
| goto_error_m      | 路径跟踪精度                      | 到位后目标不在预期像素位置         |

stop_delay_ms 的下限 1500 ms 与 HALT_REQ 的 2000 ms 预算之间只有 500 ms
余量，上限 2500 ms 已经超出预算。这是故意的：预算是均值意义上的，超出预算
但不超出 4000 ms 超时的情况必须在桩上出现，否则没人会去测"复核偶尔慢一点
会怎样"。
"""
from __future__ import annotations

import threading
from typing import Callable

import numpy as np

from patrol.common.clock import mono_ns
from patrol.drivers.base import (ChassisCaps, ChassisState, ChassisStatus,
                                 ExecHandle, ExecProgress, ExecResult, IChassis,
                                 ParamOutOfRange)
from patrol.scene.world import World

_TICK_HZ = 50.0


class _Job:
    """一次异步动作的内部记录。"""

    __slots__ = ("kind", "handle", "t0_ns", "progress", "fail_reason",
                 "target_s", "dropped", "goal_wp")

    def __init__(self, kind: str, handle: ExecHandle, dropped: bool = False):
        self.kind = kind
        self.handle = handle
        self.t0_ns = handle.issued_ts_mono_ns
        self.progress = ExecProgress.IN_PROGRESS
        self.fail_reason: str | None = None
        self.target_s: float = 0.0
        self.dropped = dropped
        self.goal_wp: str | None = None


class ChassisStub(IChassis):
    def __init__(self, cfg, world: World, seed: int = 0):
        c = cfg.get("stub.chassis")
        self.cfg, self.world = cfg, world
        self.rng = np.random.default_rng(seed)

        self._stop_delay = tuple(c.get("stop_delay_ms", [1500, 2500]))
        self._ack_drop = float(c.get("ack_drop_rate", 0.02))
        self._safety_rate = float(c.get("safety_event_rate_per_min", 0.05))
        self._safety_types = dict(c.get("safety_event_types", {"OBSTACLE_DETECTED": 1.0}))
        self._brake_lat = tuple(c.get("brake_latency_ms", [40, 95]))
        self._goto_sigma = float(c.get("goto_error_sigma_m", 0.08))
        self._drain = float(c.get("battery_drain_pct_per_min", 0.4))
        self._max_creep = float(c.get("max_creep_m", 0.5))
        self._caps = ChassisCaps(
            supports_task_level=bool(c.get("supports_task_level", True)),
            max_speed_mps=float(world.route_speed) * 2.0,
            max_creep_m=self._max_creep,
            has_safety_layer=bool(c.get("has_safety_layer", True)),
            waypoint_ids=sorted(world.waypoints),
        )

        self._lock = threading.RLock()
        self._state = ChassisState.MOVING
        self._speed = float(world.route_speed)
        self._travelled = 0.0
        self._battery = 100.0
        self._safety_active = False
        self._safety_cbs: list[Callable[[dict], None]] = []
        self._jobs: dict[str, _Job] = {}
        self._seq = 0
        self._stop_deadline_ns: int | None = None
        self._goal: tuple[str, float] | None = None
        self._creep_left = 0.0
        self._route_len = max(1e-6, world.route_length_m())

        self._stop_evt = threading.Event()
        self._thr = threading.Thread(target=self._loop, name="chassis_stub", daemon=True)
        self._thr.start()

    # ------------------------------------------------------------ 内部
    def _new_handle(self, kind: str) -> tuple[ExecHandle, _Job]:
        with self._lock:
            self._seq += 1
            h = ExecHandle(f"chassis-{kind.lower()}-{self._seq:04x}", mono_ns())
            # 丢包：指令在链路上丢了，句柄照给，但动作永远不会发生。
            # 状态机只能靠超时发现，这正是要测的路径。
            dropped = self.rng.random() < self._ack_drop
            job = _Job(kind, h, dropped=dropped)
            self._jobs[h.handle_id] = job
            return h, job

    def _emit_safety(self, event_type: str, severity: str = "CRITICAL") -> None:
        lat = int(self.rng.integers(int(self._brake_lat[0]), int(self._brake_lat[1]) + 1))
        ev = {
            "event_type": event_type,
            "severity": severity,
            "source": "CHASSIS_SAFETY_LAYER",
            "action_taken": "BRAKE",
            "brake_latency_ms": lat,
            "detail": "chassis_stub 注入: %s" % event_type,
            "ts_mono_ns": mono_ns(),
        }
        for cb in list(self._safety_cbs):
            try:
                cb(ev)                       # 实现方须保证 ≤20 ms，回调内不得阻塞
            except Exception:                # noqa: BLE001
                pass

    def _loop(self) -> None:
        dt = 1.0 / _TICK_HZ
        while not self._stop_evt.wait(dt):
            with self._lock:
                self._tick(dt)

    def _tick(self, dt: float) -> None:
        now = mono_ns()
        self._battery = max(0.0, self._battery - self._drain * dt / 60.0)

        # --- 安全事件注入 ---
        if self._state not in (ChassisState.ESTOP,):
            p = self._safety_rate * dt / 60.0
            if self.rng.random() < p:
                names = list(self._safety_types)
                w = np.array([float(self._safety_types[n]) for n in names])
                ev = names[int(self.rng.choice(len(names), p=w / w.sum()))]
                self._safety_active = True
                if ev == "ESTOP_PRESSED":
                    # 唯一不能自恢复的：必须人工解除
                    self._state = ChassisState.ESTOP
                    self._speed = 0.0
                    for j in self._jobs.values():
                        if j.progress is ExecProgress.IN_PROGRESS:
                            j.progress = ExecProgress.PREEMPTED
                else:
                    self._state = ChassisState.STOPPING
                    self._stop_deadline_ns = now + int(0.6e9)
                self._emit_safety(ev)
            elif self._safety_active and self.rng.random() < 2.0 * dt:
                self._safety_active = False   # 障碍物移开

        st = self._state
        # --- 状态推进 ---
        if st is ChassisState.STOPPING:
            self._speed = max(0.0, self._speed - 0.9 * dt)
            if self._stop_deadline_ns is not None and now >= self._stop_deadline_ns:
                self._state = ChassisState.STOPPED
                self._speed = 0.0
                self._stop_deadline_ns = None
                self._finish("PAUSE")
        elif st is ChassisState.MOVING:
            self._speed = min(self.world.route_speed, self._speed + 0.7 * dt)
            self._travelled += self._speed * dt
            if self._creep_left > 0.0:
                self._creep_left -= self._speed * dt
                if self._creep_left <= 0.0:
                    self._creep_left = 0.0
                    self._state = ChassisState.STOPPED
                    self._speed = 0.0
                    self._finish("CREEP_FORWARD")
            if self._goal is not None:
                wp_id, tol = self._goal
                wp = self.world.waypoints.get(wp_id)
                if wp is not None:
                    x, y, _, _ = self.world.pose_at(self._travelled)
                    if float(np.hypot(x - wp.x_m, y - wp.y_m)) <= max(tol, 0.15):
                        self._goal = None
                        self._state = ChassisState.STOPPED
                        self._speed = 0.0
                        self._finish("GOTO_OBSERVE")
        elif st is ChassisState.RETURNING:
            self._state = ChassisState.MOVING

    def _finish(self, kind: str) -> None:
        for j in self._jobs.values():
            if j.kind == kind and j.progress is ExecProgress.IN_PROGRESS and not j.dropped:
                j.progress = ExecProgress.DONE

    # ------------------------------------------------------------ IChassis
    def capabilities(self) -> ChassisCaps:
        return self._caps

    def pause(self, reason: str) -> ExecHandle:
        h, job = self._new_handle("PAUSE")
        with self._lock:
            if job.dropped:
                return h                    # 指令丢了，车照常走，状态机会超时
            if self._state is ChassisState.ESTOP:
                job.progress = ExecProgress.FAILED
                job.fail_reason = "ESTOP_ACTIVE"
                return h
            if self._state in (ChassisState.STOPPED, ChassisState.PAUSED):
                job.progress = ExecProgress.DONE
                return h
            delay = int(self.rng.integers(int(self._stop_delay[0]),
                                          int(self._stop_delay[1]) + 1))
            self._state = ChassisState.STOPPING
            self._stop_deadline_ns = mono_ns() + delay * 1_000_000
        return h

    def resume(self) -> ExecHandle:
        # 从任何非 ESTOP 状态都必须被接受。这条是给看门狗用的：驱动因为状态
        # 不对而拒绝，车就真的卡在路上了。
        h, job = self._new_handle("RESUME")
        with self._lock:
            if job.dropped:
                return h
            if self._state is ChassisState.ESTOP:
                job.progress = ExecProgress.FAILED
                job.fail_reason = "ESTOP_ACTIVE"
                return h
            self._state = ChassisState.MOVING
            self._stop_deadline_ns = None
            self._goal = None
            self._creep_left = 0.0
            job.progress = ExecProgress.DONE
        return h

    def creep_forward(self, distance_m: float) -> ExecHandle:
        d = float(distance_m)
        if not (0.0 < d <= self._caps.max_creep_m + 1e-9):
            # 驱动层按硬件能力校验，越界抛异常不截断（ICD §8.1 第二条）
            raise ParamOutOfRange(
                "creep_forward %.3f m 超出硬件上限 %.3f m" % (d, self._caps.max_creep_m))
        h, job = self._new_handle("CREEP_FORWARD")
        with self._lock:
            if job.dropped:
                return h
            if self._state is ChassisState.ESTOP:
                job.progress = ExecProgress.FAILED
                job.fail_reason = "ESTOP_ACTIVE"
                return h
            self._creep_left = d
            self._state = ChassisState.MOVING
        return h

    def goto_observe(self, waypoint_id: str, tolerance_m: float) -> ExecHandle:
        if waypoint_id not in self._caps.waypoint_ids:
            raise ParamOutOfRange("巡检位 %r 不在底盘标定表内" % waypoint_id)
        h, job = self._new_handle("GOTO_OBSERVE")
        with self._lock:
            if job.dropped:
                return h
            if self._state is ChassisState.ESTOP:
                job.progress = ExecProgress.FAILED
                job.fail_reason = "ESTOP_ACTIVE"
                return h
            # 到位误差：路径跟踪精度，正态 σ=0.08 m
            self._goal = (waypoint_id, float(tolerance_m)
                          + abs(float(self.rng.normal(0.0, self._goto_sigma))))
            self._state = ChassisState.MOVING
        return h

    def status(self) -> ChassisStatus:
        with self._lock:
            x, y, _, near = self.world.pose_at(self._travelled)
            dist_goal = None
            if self._goal is not None:
                wp = self.world.waypoints.get(self._goal[0])
                if wp is not None:
                    dist_goal = float(np.hypot(x - wp.x_m, y - wp.y_m))
            return ChassisStatus(
                state=self._state,
                speed_mps=round(float(self._speed), 4),
                path_progress=float(np.clip((self._travelled % self._route_len)
                                            / self._route_len, 0.0, 1.0)),
                distance_to_goal_m=dist_goal,
                current_waypoint_id=near or None,
                battery_pct=round(float(self._battery), 2),
                safety_layer_active=bool(self._safety_active),
                ts_mono_ns=mono_ns(),
            )

    def poll(self, handle: ExecHandle) -> ExecResult:
        with self._lock:
            j = self._jobs.get(handle.handle_id)
        if j is None:
            return ExecResult(ExecProgress.FAILED, 0, "未知句柄")
        return ExecResult(j.progress,
                          int((mono_ns() - j.t0_ns) // 1_000_000), j.fail_reason)

    def subscribe_safety(self, cb: Callable[[dict], None]) -> None:
        self._safety_cbs.append(cb)

    def close(self) -> None:
        self._stop_evt.set()
        self._thr.join(timeout=1.0)

    # ------------------------------------------------------------ 测试用
    def travelled_m(self) -> float:
        with self._lock:
            return self._travelled

    def force_safety_event(self, event_type: str = "OBSTACLE_DETECTED") -> None:
        """测试与演示用：手工注入一次安全事件。"""
        with self._lock:
            self._safety_active = True
            if event_type == "ESTOP_PRESSED":
                self._state = ChassisState.ESTOP
                self._speed = 0.0
            else:
                self._state = ChassisState.STOPPING
                self._stop_deadline_ns = mono_ns() + int(0.6e9)
        self._emit_safety(event_type)

    def clear_estop(self) -> None:
        """急停必须人工解除。"""
        with self._lock:
            if self._state is ChassisState.ESTOP:
                self._state = ChassisState.STOPPED
                self._safety_active = False
