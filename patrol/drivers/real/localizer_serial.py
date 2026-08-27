"""定位真机驱动。

**本项目的定位不是一台独立设备。**方案里的定位来自底盘的 SLAM/里程计，
经同一条串口以状态帧的形式上来（`STA` 的 `progress_permille` 与
`waypoint`）。所以这个类不开新链路，直接问 `ChassisSerial` 要状态，再按
标定表把"路线完成比例"换算成 map 系位姿。

**这会不会太弱？**对本课题够用，且边界写清楚了：

- **`source` 恒为 `ODOM_ONLY`。**车上没有激光雷达，位置是拿底盘上报的
  `path_progress` 沿标定路线插出来的，属于航位推算。报 `LIDAR_SLAM` 是撒谎，
  而这个字段会原样进证据包——将来查一次定位偏差时，它是判断"该信多少"的
  唯一依据。等真上了 SLAM 模块再改这里。
- `valid` 取底盘健康状态。`FAULT` / `ESTOP` 时置 false，触发 ICD §7.3 的
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
        # 路线几何。**真机模式不许读 scene.\***——那是虚拟配电室的配置，
        # 只在 stub 模式存在。原来这里写的是 cfg.get("scene.route.points")，
        # 而 system.yaml 无条件 include 了 scene.yaml，于是真机跑起来会拿
        # 虚拟场景的巡检顺序去插值真车的位置，错得毫无征兆。
        self._wps = [(w["id"], float(w["x_m"]), float(w["y_m"]))
                     for w in cfg.get("waypoints")]
        self._route = (list(c.get("route_points", []))
                       or [w[0] for w in self._wps])   # 缺省按标定表顺序
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
                     # 车上没有激光雷达，这就是航位推算。见模块文档。
                     source=PoseSource.ODOM_ONLY,
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
