"""定位桩。ICD §9.3。

按标定路线程序生成位姿序列，注入噪声与**定位失锁**。

失锁必须注入，因为 POSE_INVALID 抑制规则的正确性只能靠它验证：失锁期间
状态机应当继续巡航但不发起复核（GOTO_OBSERVE 在漂移的坐标系里没有意义），
失锁恢复后被压下的事件按 priority 排队重试。这条逻辑没有失锁注入就是死代码。
"""
from __future__ import annotations

import math
import threading
from typing import Callable

import numpy as np

from patrol.common.clock import mono_ns
from patrol.drivers.base import ILocalizer, Pose, PoseSource
from patrol.scene.world import World


class PoseStub(ILocalizer):
    def __init__(self, cfg, world: World, chassis, seed: int = 0):
        c = cfg.get("stub.pose")
        self.world, self._chassis = world, chassis
        self.rng = np.random.default_rng(seed)
        self._rate = float(c.get("rate_hz", 20.0))
        self._sigma = float(c.get("noise_sigma_m", 0.02))
        self._lost_rate = float(c.get("lost_rate_per_min", 0.02))
        self._lost_dur = tuple(c.get("lost_duration_ms", [3000, 12000]))
        self._drift = float(c.get("drift_mps", 0.05))

        self._lock = threading.RLock()
        self._source = PoseSource.LIDAR_SLAM
        self._lost_until_ns = 0
        self._drift_xy = np.zeros(2)
        self._pose = self._sample()
        self._cbs: list[Callable[[Pose], None]] = []
        self._stop = threading.Event()
        self._thr = threading.Thread(target=self._loop, name="pose_stub", daemon=True)
        self._thr.start()

    # ------------------------------------------------------------ 内部
    def _sample(self) -> Pose:
        x, y, yaw, _ = self.world.pose_at(self._chassis.travelled_m())
        nx = float(self.rng.normal(0.0, self._sigma))
        ny = float(self.rng.normal(0.0, self._sigma))
        x += nx + float(self._drift_xy[0])
        y += ny + float(self._drift_xy[1])
        lost = self._source is not PoseSource.LIDAR_SLAM
        # 协方差迹：失锁期间随漂移量增长，mission 据此判断定位是否可信
        cov = 0.014 + (float(np.linalg.norm(self._drift_xy)) ** 2) * 0.5
        return Pose(
            x_m=round(x, 4), y_m=round(y, 4),
            yaw_deg=round(((yaw + 180.0) % 360.0) - 180.0, 4),
            cov_trace=round(cov, 6),
            valid=not lost,
            source=self._source,
            ts_mono_ns=mono_ns(),
        )

    def _loop(self) -> None:
        dt = 1.0 / max(1.0, self._rate)
        while not self._stop.wait(dt):
            with self._lock:
                now = mono_ns()
                if self._source is PoseSource.LIDAR_SLAM:
                    if self.rng.random() < self._lost_rate * dt / 60.0:
                        self._enter_lost(now)
                else:
                    # 纯里程计：误差随时间累积
                    ang = float(self.rng.uniform(0, 2 * math.pi))
                    self._drift_xy += np.array([math.cos(ang), math.sin(ang)]) * self._drift * dt
                    if now >= self._lost_until_ns:
                        self._source = PoseSource.LIDAR_SLAM
                        self._drift_xy = np.zeros(2)
                p = self._sample()
                self._pose = p
            for cb in list(self._cbs):
                try:
                    cb(p)
                except Exception:            # noqa: BLE001
                    pass

    def _enter_lost(self, now_ns: int) -> None:
        self._source = PoseSource.ODOM_ONLY
        dur = int(self.rng.integers(int(self._lost_dur[0]), int(self._lost_dur[1]) + 1))
        self._lost_until_ns = now_ns + dur * 1_000_000
        self._drift_xy = np.zeros(2)

    # ------------------------------------------------------------ ILocalizer
    def get_pose(self) -> Pose:
        with self._lock:
            return self._pose

    def subscribe(self, cb: Callable[[Pose], None]) -> None:
        self._cbs.append(cb)

    def close(self) -> None:
        self._stop.set()
        self._thr.join(timeout=1.0)

    # ------------------------------------------------------------ 测试用
    def force_lost(self, duration_ms: int = 5000) -> None:
        """测试与演示用：手工触发一次定位失锁，验证 POSE_INVALID 抑制。"""
        with self._lock:
            self._source = PoseSource.ODOM_ONLY
            self._lost_until_ns = mono_ns() + int(duration_ms) * 1_000_000
            self._drift_xy = np.zeros(2)
