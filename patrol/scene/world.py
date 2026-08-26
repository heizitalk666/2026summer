"""虚拟配电室的世界模型。

目标的 map 系位置、朝向、物理尺寸与**真值**都在这里。真值只有两个用途：

1. 渲染时按它把指针画到该在的角度
2. 测试时给读数算法打分

绝不允许感知侧读到它。`Target.truth` 与 `Target.priors` 分开正是为此：
priors 是系统合法可知的先验（类别的物理尺寸查表值、量程铭牌），
truth 是只有"上帝"知道的当前读数。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from patrol.scene.optics import PinholeCamera


@dataclass(frozen=True)
class Waypoint:
    id: str
    x_m: float
    y_m: float
    yaw_deg: float
    note: str = ""

    @property
    def xy(self) -> tuple[float, float]:
        return self.x_m, self.y_m


@dataclass
class Target:
    """一个被测对象。"""

    id: str
    defect_class: str          # DetectionEvent.detections[].defect_class
    waypoint: str              # 负责观测它的巡检位
    position: np.ndarray       # map 系 (x, y, z)，米
    facing_deg: float          # 表盘法线的方位角，map 系
    diameter_m: float          # 物理尺寸，即 target_size_m 的先验查表值
    truth: dict[str, Any] = field(default_factory=dict)
    anomalous: bool = False

    # ---- 先验：系统合法可知的部分 ----
    @property
    def priors(self) -> dict[str, Any]:
        """标定阶段就录入的信息，感知侧可以读。不含当前读数。"""
        t = self.truth
        return {
            "kind": t.get("kind"),
            "unit": t.get("unit"),
            "range_min": t.get("range_min"),
            "range_max": t.get("range_max"),
            "sweep_deg": t.get("sweep_deg", 270.0),
            "zero_offset_deg": t.get("zero_offset_deg", -135.0),
            "normal_band": t.get("normal_band"),
            "normal_states": t.get("normal_states"),
            "major_ticks": t.get("major_ticks", 27),
            "target_size_m": self.diameter_m,
        }

    @property
    def true_value(self):
        """当前真值。**只用于渲染与打分。**"""
        return self.truth.get("value")

    def corners(self, half: float | None = None) -> np.ndarray:
        """目标平面上的四个角点（map 系），顺序为 左上/右上/右下/左下。

        平面法线朝向 facing_deg，平面内的"右"方向由法线与世界上方叉乘得到。
        """
        h = self.diameter_m / 2.0 if half is None else half
        a = math.radians(self.facing_deg)
        n = np.array([math.cos(a), math.sin(a), 0.0])       # 法线，指向观察者
        up = np.array([0.0, 0.0, 1.0])
        right = np.cross(up, n)
        right /= max(1e-12, np.linalg.norm(right))
        p = self.position
        return np.array([p - right * h + up * h, p + right * h + up * h,
                         p + right * h - up * h, p - right * h - up * h])

    def facing_cosine(self, cam_origin: np.ndarray) -> float:
        """观察方向与表盘法线的夹角余弦。1 = 正对，≤0 = 从背面看。

        用于两件事：判可见性；以及决定椭圆拟合的长短轴比 b/a，方案书 §6.1.4
        规定 b/a < 0.85 时视角倾斜过大，应由状态机下发云台角度调整重新拍摄。
        """
        a = math.radians(self.facing_deg)
        n = np.array([math.cos(a), math.sin(a), 0.0])
        v = np.asarray(cam_origin, dtype=float) - self.position
        d = np.linalg.norm(v)
        return 0.0 if d < 1e-9 else float(np.dot(n, v / d))


class World:
    """场景 + 巡检位 + 路线。"""

    def __init__(self, cfg):
        self.cfg = cfg
        sc = cfg.get("scene")
        self.room = sc.get("room", {})
        self.lighting = sc.get("lighting", {})
        self.noise = sc.get("noise", {})
        self.waypoints: dict[str, Waypoint] = {
            w["id"]: Waypoint(w["id"], float(w["x_m"]), float(w["y_m"]),
                              float(w["yaw_deg"]), w.get("note", ""))
            for w in cfg.get("waypoints")
        }
        self.targets: list[Target] = []
        for t in sc.get("targets", []):
            p = t["position"]
            self.targets.append(Target(
                id=t["id"], defect_class=t["class"], waypoint=t.get("waypoint", ""),
                position=np.array([float(p["x_m"]), float(p["y_m"]), float(p["z_m"])]),
                facing_deg=float(t.get("facing_deg", 180.0)),
                diameter_m=float(t.get("diameter_m", 0.15)),
                truth=dict(t.get("truth", {})),
                anomalous=bool(t.get("anomalous", False)),
            ))
        self.route_ids: list[str] = list(sc.get("route", {}).get("points", []))
        self.route_speed = float(sc.get("route", {}).get("speed_mps", 0.5))
        self.route_loop = bool(sc.get("route", {}).get("loop", True))
        self.camera_height_m = float(sc.get("camera_height_m", 1.20))

    # ---- 目标查询 ---------------------------------------------------
    def by_id(self, tid: str) -> Target | None:
        return next((t for t in self.targets if t.id == tid), None)

    def at_waypoint(self, wp_id: str) -> list[Target]:
        return [t for t in self.targets if t.waypoint == wp_id]

    def visible(self, cam: PinholeCamera, *, margin_px: float = 80.0,
                min_facing_cos: float = 0.15) -> list[tuple[Target, np.ndarray]]:
        """从相机看得见的目标，返回 (目标, 四角像素坐标)。

        剔除三种情况：在相机背后、四角全在画幅外、从表盘背面看。
        """
        out = []
        for t in self.targets:
            if t.facing_cosine(cam.origin) < min_facing_cos:
                continue
            uv, z = cam.project(t.corners())
            if np.any(z <= 1e-6):
                continue
            inside = cam.in_view(uv, z, margin_px=margin_px)
            if not np.any(inside):
                continue
            out.append((t, uv))
        return out

    def distance_to(self, t: Target, cam_origin) -> float:
        return float(np.linalg.norm(np.asarray(cam_origin, float) - t.position))

    # ---- 路线 -------------------------------------------------------
    def route_points(self) -> list[Waypoint]:
        return [self.waypoints[i] for i in self.route_ids if i in self.waypoints]

    def route_length_m(self) -> float:
        pts = self.route_points()
        if len(pts) < 2:
            return 0.0
        seq = pts + ([pts[0]] if self.route_loop else [])
        return float(sum(math.dist(seq[i].xy, seq[i + 1].xy)
                         for i in range(len(seq) - 1)))

    def pose_at(self, travelled_m: float) -> tuple[float, float, float, str]:
        """沿路线走了 travelled_m 米之后的位姿 (x, y, yaw_deg, 最近巡检位)。

        pose_stub 用它生成位姿序列，chassis_stub 用它推进 path_progress。
        yaw 取当前路段的方向，转角处线性过渡由 pose_stub 的滤波自然平滑。
        """
        pts = self.route_points()
        if not pts:
            return 0.0, 0.0, 0.0, ""
        if len(pts) == 1:
            return pts[0].x_m, pts[0].y_m, pts[0].yaw_deg, pts[0].id
        seq = pts + ([pts[0]] if self.route_loop else [])
        total = self.route_length_m()
        s = (travelled_m % total) if (self.route_loop and total > 0) else min(travelled_m, total)
        acc = 0.0
        for i in range(len(seq) - 1):
            a, b = seq[i], seq[i + 1]
            seg = math.dist(a.xy, b.xy)
            if seg <= 1e-9:
                continue
            if acc + seg >= s or i == len(seq) - 2:
                f = float(np.clip((s - acc) / seg, 0.0, 1.0))
                x = a.x_m + (b.x_m - a.x_m) * f
                y = a.y_m + (b.y_m - a.y_m) * f
                yaw = math.degrees(math.atan2(b.y_m - a.y_m, b.x_m - a.x_m))
                near = a.id if f < 0.5 else b.id
                return x, y, ((yaw + 180.0) % 360.0) - 180.0, near
            acc += seg
        return seq[-1].x_m, seq[-1].y_m, seq[-1].yaw_deg, seq[-1].id

    def nearest_waypoint(self, x: float, y: float) -> tuple[str, float]:
        best, bd = "", float("inf")
        for w in self.waypoints.values():
            d = math.dist((x, y), w.xy)
            if d < bd:
                best, bd = w.id, d
        return best, bd
