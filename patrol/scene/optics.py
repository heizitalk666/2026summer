"""光学模型：视场角、像素密度、针孔投影。

**本模块是整个项目立论的落点。**

方案书 §5.3 的推导链：

    0.5 % FS 精度要求
      → 误差传递（指针尖端 ±1 px、表盘中心 ±1 px，方和根合成）
      → 表盘半径 r ≥ 60 px，即直径 p_min = 120 px
      → 巡航 5 m 处只有 50 px，不够
      → 所需变焦 z_req = 120/50 = 2.4，取 3×
      → 3× 已到上限时反解距离上限 d_max ≈ 6.2 m，取 6 m

像素密度公式（方案书 §5.3.1 / ICD §3.2）：

    p = W · D · z / (2 · d · tan(θ/2))

**关键实现选择**：变焦在这里实现为视场角的收缩

    hfov(z) = 2 · arctan( tan(θ₀/2) / z )

于是 tan(hfov/2) = tan(θ₀/2)/z，代回针孔投影公式

    p = W · D / (2 · d · tan(hfov/2)) = W · D · z / (2 · d · tan(θ₀/2))

正好还原成上式。也就是说：渲染器按针孔模型画图，画出来的表盘像素宽度
**自动**满足像素密度公式，不需要任何额外校正。这条等价关系是
tests/test_pixel_density.py 要验证的东西——如果渲染图上量出来的 p 和
公式算的对不上，说明投影实现错了。

ICD §3.2 还留了个坑：像素密度公式里的 θ 可以取"当前变焦下的实际 hfov"，
也可以取"广角端 θ₀ 与 zoom 一起代入"，两种算法必须二选一并注释清楚，
不能混用。本实现统一用后者（θ₀ + z），见 pixel_density()。
"""
from __future__ import annotations

import math

import numpy as np


def hfov_at_zoom(hfov_at_1x_deg: float, zoom: float) -> float:
    """当前变焦倍率下的实际水平视场角，度。

    对应 StatusReport.ptz.hfov_deg 与 DetectionEvent.context.ptz.hfov_deg。
    """
    z = max(1e-6, float(zoom))
    return math.degrees(2.0 * math.atan(math.tan(math.radians(hfov_at_1x_deg) / 2.0) / z))


def vfov_from_hfov(hfov_deg: float, width: int, height: int) -> float:
    """由水平视场角与画幅比例推出垂直视场角（方形像素）。"""
    return math.degrees(
        2.0 * math.atan(math.tan(math.radians(hfov_deg) / 2.0) * height / width)
    )


def focal_px(width: int, hfov_deg: float) -> float:
    """针孔模型的焦距，单位像素。f = W / (2·tan(hfov/2))。"""
    return width / (2.0 * math.tan(math.radians(hfov_deg) / 2.0))


def pixel_density(width: int, target_size_m: float, zoom: float,
                  distance_m: float, hfov_at_1x_deg: float) -> float:
    """p = W·D·z / (2·d·tan(θ₀/2))。ICD §3.2 的两种算法里取"θ₀ 与 z 一起代入"。"""
    d = max(1e-6, float(distance_m))
    return (width * float(target_size_m) * float(zoom)) / (
        2.0 * d * math.tan(math.radians(hfov_at_1x_deg) / 2.0)
    )


def distance_for_density(width: int, target_size_m: float, zoom: float,
                         want_px: float, hfov_at_1x_deg: float) -> float:
    """反解：要达到 want_px 的像素密度，距离最多多少米。"""
    return (width * float(target_size_m) * float(zoom)) / (
        2.0 * float(want_px) * math.tan(math.radians(hfov_at_1x_deg) / 2.0)
    )


def zoom_for_density(cur_zoom: float, cur_px: float, want_px: float,
                     max_zoom: float = 3.0) -> float:
    """按需变焦（差异清单 C4，方案书 §6.3.5）。

        z_cmd = clip(z_cur × p_target / p_cur, 1, max_zoom)

    p 与 z 严格成正比，所以变焦不需要连续调节，可以一步算到位。ICD §7.2 的
    ZOOM 状态固定下发 zoom=3，对近距离目标会过度放大导致目标出框——方案书
    §9.4 的问题预案里"变焦后目标丢失"写的就是这个。
    """
    if cur_px <= 0:
        return float(max_zoom)
    return float(np.clip(cur_zoom * want_px / cur_px, 1.0, max_zoom))


def stub_effective_pixel_ratio(zoom: float, source_width: int = 3840,
                               out_width: int = 1920) -> float:
    """ptz_stub 的有效感光像素比 k = min(1, (source/out)/z)。ICD §9.2。

    4K 裁剪仿真在 z 倍变焦时取 source/z 宽的 ROI 再缩放到 out 宽。z 小于
    source/out 时是降采样（信息足够，截断为 1.0），大于时是上采样，真实
    感光像素不够。

    默认 3840→1920 时 k = min(1, 2/z)：z=3 时 k=2/3，桩只有真机 2/3 的
    信息量，故桩上 d_max = 4.16 m 而非真机的 6.24 m。**这条差异必须写进
    M2 的验收条件**——桩环境的标定素材要把表计目标布在 4 m 以内，否则 L2
    读数会在桩上大面积失败，而失败原因是仿真损失不是算法问题。
    """
    z = max(1e-6, float(zoom))
    return min(1.0, (float(source_width) / float(out_width)) / z)


# ---------------------------------------------------------------- 位姿与投影
def rot_z(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


class PinholeCamera:
    """针孔相机。

    坐标系（ICD §2.3）：

    - ``map``     建图时确定的固定 ENU 系，x 东 y 北 z 天
    - ``camera``  相机光心，x 右 y 下 z 前

    云台的 pan / tilt 相对 ``base_link`` 定义，不是相对 ``map``——这样车体
    转向时不需要重算云台角度。所以相机方位角 = 车体 yaw + pan。
    """

    __slots__ = ("width", "height", "hfov_deg", "f_px", "cx", "cy",
                 "origin", "R")

    def __init__(self, width: int, height: int, hfov_deg: float,
                 origin_m: tuple[float, float, float],
                 yaw_deg: float, pan_deg: float, tilt_deg: float):
        self.width, self.height = int(width), int(height)
        self.hfov_deg = float(hfov_deg)
        self.f_px = focal_px(self.width, self.hfov_deg)
        self.cx, self.cy = self.width / 2.0, self.height / 2.0
        self.origin = np.asarray(origin_m, dtype=float)

        az = math.radians(yaw_deg + pan_deg)     # 方位角，map 系内 CCW 为正
        el = math.radians(tilt_deg)              # 俯仰角，抬头为正
        fwd = np.array([math.cos(el) * math.cos(az),
                        math.cos(el) * math.sin(az),
                        math.sin(el)])
        world_up = np.array([0.0, 0.0, 1.0])
        right = np.cross(fwd, world_up)
        n = np.linalg.norm(right)
        # 云台俯仰限位是 [-30, 60]，不会真的看正上方，这里只是兜底
        right = np.array([0.0, -1.0, 0.0]) if n < 1e-9 else right / n
        down = np.cross(fwd, right)
        down /= max(1e-12, np.linalg.norm(down))
        # R 的列是相机系基向量在 map 系下的表示
        self.R = np.column_stack([right, down, fwd])

    # -- 投影 ---------------------------------------------------------
    def to_camera(self, pts_world: np.ndarray) -> np.ndarray:
        """map 系点 → camera 系点。pts 形状 (N, 3)。"""
        p = np.atleast_2d(np.asarray(pts_world, dtype=float)) - self.origin
        return p @ self.R                     # 等价于 (R^T @ p.T).T

    def project(self, pts_world: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """map 系点 → 像素坐标。返回 (uv, z_cam)，z_cam ≤ 0 表示在相机背后。"""
        pc = self.to_camera(pts_world)
        z = pc[:, 2]
        safe = np.where(np.abs(z) < 1e-9, 1e-9, z)
        u = self.f_px * pc[:, 0] / safe + self.cx
        v = self.f_px * pc[:, 1] / safe + self.cy
        return np.column_stack([u, v]), z

    def in_view(self, uv: np.ndarray, z: np.ndarray, margin_px: float = 0.0) -> np.ndarray:
        return ((z > 1e-6)
                & (uv[:, 0] >= -margin_px) & (uv[:, 0] < self.width + margin_px)
                & (uv[:, 1] >= -margin_px) & (uv[:, 1] < self.height + margin_px))

    def aim_offset_deg(self, pt_world) -> tuple[float, float]:
        """把某个 map 系点转到画面中心所需的云台增量（pan, tilt），度。

        对应 DetectionEvent.detections[].aim_offset。注意这是**前馈**量，
        A1 的 PID 伺服用它做初值，之后靠像素偏差闭环。
        """
        pc = self.to_camera(np.asarray([pt_world], dtype=float))[0]
        x, y, z = float(pc[0]), float(pc[1]), float(pc[2])
        # 符号约定：pan 正方向是 CCW（base_link 的 +y 是左），所以目标落在
        # 相机系 x>0（右侧）时需要 pan 减小，故取负号。tilt 正方向是抬头，
        # 相机系 y 向下，目标在画面上方时 y<0，取负号后为正，正确。
        # 与 ICD §3.5/§4.6 的示例一致：aim_offset 是加到当前位姿上的增量
        # （context.ptz.tilt_deg=-2.0，aim_offset.tilt_deg=1.6，
        #   PTZ_SET.tilt_deg=-0.4，即 -2.0+1.6=-0.4）。
        pan = -math.degrees(math.atan2(x, max(1e-9, z)))
        tilt = -math.degrees(math.atan2(y, math.hypot(x, z)))
        return pan, tilt


def bbox_from_points(uv: np.ndarray) -> tuple[float, float, float, float]:
    """一组像素点的外接框 [x1, y1, x2, y2]。"""
    return (float(uv[:, 0].min()), float(uv[:, 1].min()),
            float(uv[:, 0].max()), float(uv[:, 1].max()))
