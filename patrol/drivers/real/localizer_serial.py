"""定位真机驱动。

**本项目的定位不是一台独立设备。**方案里的定位来自底盘的 SLAM/里程计，
经同一条串口以状态帧的形式上来（`STA` 的 `progress_permille` 与
`waypoint`）。所以这个类不开新链路，直接问 `ChassisSerial` 要状态，再按
标定表把"路线完成比例"换算成 map 系位姿。

**这会不会太弱？**对本课题够用，且边界写清楚了：

- `cov_trace` 直接取底盘上报的定位质量。底盘只报 `progress` 时退化为按
  路线插值，此时 `source = ODOM_ONLY`、`valid = False`，触发 ICD §7.3 的
  `POSE_INVALID` 抑制——**宁可不复核，也不在漂移的坐标系里下发 GOTO_OBSERVE**。
- 若后续换成独立的激光 SLAM 模块（另一条串口或 ROS 话题），只改本文件，
  ILocalizer 的四个方法语义不变。

这条也是给硬件组的对接清单里"巡检位编号在底盘侧如何标定与存储"那一项的
上位机侧实现。
"""
from __future__ import annotations

import math
import threading
from typing import Callable

from patrol.common.clock import mono_ns
from patrol.common.errors import DriverNotReady
from patrol.drivers.base import ILocalizer, Pose, PoseSource


class LocalizerSerial(ILocalizer):
    def __init__(self, cfg, chassis, world=None):
        c = dict(cfg.get("real.localizer", {}))
        self._chassis = chassis
        self._rate = float(c.get("rate_hz", 20.0))
        self._cov_ok = float(c.get("cov_trace_nominal", 0.014))
        self._cov_odom = float(c.get("cov_trace_odom", 0.35))
        # 路线几何：真机模式不加载 scene，只用巡检位标定表插值
        self._wps = [(w["id"], float(w["x_m"]), float(w["y_m"]))
                     for w in cfg.get("waypoints")]
        self._route = list(cfg.get("scene.route.points", [])) or [w[0] for w in self._wps]
        self._by_id = {w[0]: (w[1], w[2]) for w in self._wps}

        self._lock = threading.RLock()
        self._pose: Pose | None = None
        self._cbs: list[Callable[[Pose], None]] = []
        self._stop = threading.Event()
        self._thr = threading.Thread(target=self._loop, name="localizer_serial",
                                     daemon=True)
        self._thr.start()

    # ------------------------------------------------------------
    def _polyline(self) -> list[tuple[float, float]]:
        pts = [self._by_id[i] for i in self._route if i in self._by_id]
        return pts + pts[:1] if len(pts) > 1 else pts

    def _pose_at(self, progress: float) -> tuple[float, float, float]:
        """按路线完成比例插值出位姿。yaw 取当前路段方向。"""
        pts = self._polyline()
        if len(pts) < 2:
            x, y = (pts[0] if pts else (0.0, 0.0))
            return x, y, 0.0
        segs = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        total = sum(segs) or 1.0
        s = max(0.0, min(1.0, float(progress))) * total
        acc = 0.0
        for i, seg in enumerate(segs):
            if seg <= 1e-9:
                continue
            if acc + seg >= s or i == len(segs) - 1:
                f = min(1.0, max(0.0, (s - acc) / seg))
                a, b = pts[i], pts[i + 1]
                yaw = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f,
                        ((yaw + 180.0) % 360.0) - 180.0)
            acc += seg
        return pts[-1][0], pts[-1][1], 0.0

    def _loop(self) -> None:
        dt = 1.0 / max(1.0, self._rate)
        while not self._stop.wait(dt):
            try:
                st = self._chassis.status()
            except DriverNotReady:
                continue
            x, y, yaw = self._pose_at(st.path_progress)
            # 底盘只给完成比例，没有独立的定位质量 → 只能算里程计级
            valid = st.state.value not in ("FAULT", "ESTOP")
            p = Pose(x_m=round(x, 4), y_m=round(y, 4), yaw_deg=round(yaw, 4),
                     cov_trace=self._cov_ok if valid else self._cov_odom,
                     valid=valid,
                     source=PoseSource.ODOM_ONLY if not valid else PoseSource.LIDAR_SLAM,
                     ts_mono_ns=mono_ns())
            with self._lock:
                self._pose = p
            for cb in list(self._cbs):
                try:
                    cb(p)
                except Exception:            # noqa: BLE001
                    pass

    # ------------------------------------------------------------ ILocalizer
    def get_pose(self) -> Pose:
        with self._lock:
            if self._pose is None:
                raise DriverNotReady("尚未从底盘状态帧解出位姿")
            return self._pose

    def subscribe(self, cb: Callable[[Pose], None]) -> None:
        self._cbs.append(cb)

    def close(self) -> None:
        self._stop.set()
        self._thr.join(timeout=1.0)
