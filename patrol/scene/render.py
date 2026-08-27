"""虚拟配电室渲染器。

给定 (车位姿, pan, tilt, zoom)，渲染一张 1920×1080 的 BGR 图像。

**这是无硬件条件下整套系统能跑起来的支点。**渲染严格走针孔投影，所以
表盘在图上的像素宽度自动满足 p = W·D·z/(2·d·tan(θ₀/2))——不是渲染完再
去凑公式，而是公式本来就是投影的推论（见 optics 模块文档）。

渲染管线（远到近）：

    地面与墙 → 柜列 → 目标贴图（透视映射）→ 表盘玻璃高光
    → 4K 裁剪仿真的信息损失 → 景深模糊 → 运动模糊 → 低照度噪声 → 暗角

其中"4K 裁剪仿真的信息损失"对应 ICD §9.2：桩用 4K 素材裁剪缩放仿真云台，
z 倍变焦时有效感光像素比 k = min(1, 2/z)。这里的实现方式是**先按 k 折算
后的分辨率渲染，再放大回 1920×1080**——这样丢掉的信息是真的丢了，和
4K 裁剪上采样的效果一致。桩上 d_max 因此是 4.16 m 而非真机的 6.24 m。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from patrol.scene import gauges
from patrol.scene.optics import (PinholeCamera, hfov_at_zoom,
                                 stub_effective_pixel_ratio)
from patrol.scene.world import Target, World

_FLOOR = (86, 86, 90)
_WALL = (150, 148, 142)
_CEIL = (176, 174, 168)
_CABINET = (142, 140, 134)
_CABINET_EDGE = (96, 95, 92)


@dataclass
class RenderOptions:
    width: int = 1920
    height: int = 1080
    hfov_at_1x_deg: float = 60.0
    simulate_4k_crop: bool = True
    source_width: int = 3840
    speed_mps: float = 0.0        # 用于运动模糊
    exposure_gain: float = 1.0
    draw_debug: bool = False      # 画出目标框与像素密度，仅 viewer 用


class SceneRenderer:
    def __init__(self, world: World, opts: RenderOptions | None = None,
                 seed: int = 0):
        self.world = world
        self.o = opts or RenderOptions()
        self.rng = np.random.default_rng(seed)
        self._tex_cache: dict[tuple, np.ndarray] = {}

    # ------------------------------------------------------------ 贴图
    def _texture(self, t: Target, want_px: int) -> np.ndarray:
        """目标贴图。按需要的分辨率生成并缓存，避免每帧重画。"""
        # 量化到 2 的幂附近，减少缓存键的种类
        size = int(min(512, max(48, 1 << int(math.ceil(math.log2(max(48, want_px)))))))
        key = (t.id, size, str(t.true_value))
        hit = self._tex_cache.get(key)
        if hit is not None:
            return hit
        p = t.priors
        kind = str(p.get("kind") or "")
        if kind == "POINTER_GAUGE":
            tex = gauges.render_pointer_gauge(
                size, value=float(t.true_value), range_min=float(p["range_min"]),
                range_max=float(p["range_max"]), sweep_deg=float(p["sweep_deg"]),
                zero_offset_deg=float(p["zero_offset_deg"]),
                major_ticks=int(p["major_ticks"]), unit=str(p.get("unit") or ""),
                normal_band=tuple(p["normal_band"]) if p.get("normal_band") else None,
            )
        elif kind == "INDICATOR_LIGHT":
            tex = gauges.render_indicator_light(size, color=str(t.true_value), on=True)
        elif kind == "SWITCH_POSITION":
            tex = gauges.render_switch_handle(size, position=str(t.true_value))
        else:
            tex = gauges.render_anomaly_object(size, seed=abs(hash(t.id)) % 9999)
        self._tex_cache[key] = tex
        return tex

    # ------------------------------------------------------------ 基元
    @staticmethod
    def _clip_near(pts_cam: np.ndarray, near: float = 0.05) -> np.ndarray:
        """相机系多边形对近平面 z = near 做 Sutherland-Hodgman 裁剪。

        地面、天花板、墙这些大面通常跨越相机平面（一部分在身后），整块丢弃
        会让房间消失，不裁直接投影则会因除以接近零的 z 产生天文数字坐标。
        """
        n = len(pts_cam)
        if n == 0:
            return pts_cam
        out = []
        for i in range(n):
            cur, nxt = pts_cam[i], pts_cam[(i + 1) % n]
            cin, nin = cur[2] >= near, nxt[2] >= near
            if cin:
                out.append(cur)
            if cin != nin:
                t = (near - cur[2]) / (nxt[2] - cur[2])
                out.append(cur + t * (nxt - cur))
        return np.array(out, dtype=float) if out else np.empty((0, 3))

    def _project_polygon(self, cam: PinholeCamera, pts_world) -> np.ndarray | None:
        """世界多边形 → 裁剪后的像素多边形。不可见返回 None。"""
        pc = cam.to_camera(np.asarray(pts_world, dtype=float))
        pc = self._clip_near(pc)
        if len(pc) < 3:
            return None
        u = cam.f_px * pc[:, 0] / pc[:, 2] + cam.cx
        v = cam.f_px * pc[:, 1] / pc[:, 2] + cam.cy
        # 裁剪后仍可能远超画幅，夹到一个安全范围避免 fillConvexPoly 溢出
        lim = 10 * max(cam.width, cam.height)
        uv = np.column_stack([np.clip(u, -lim, lim), np.clip(v, -lim, lim)])
        if (uv[:, 0].max() < 0 or uv[:, 0].min() > cam.width
                or uv[:, 1].max() < 0 or uv[:, 1].min() > cam.height):
            return None
        return uv

    @staticmethod
    def _fill_quad(img: np.ndarray, uv: np.ndarray, color, edge=None) -> None:
        pts = np.round(uv).astype(np.int32)
        # 凸多边形与半空间的交仍是凸的，裁剪后用 fillConvexPoly 依然正确
        cv2.fillConvexPoly(img, pts, color, cv2.LINE_AA)
        if edge is not None:
            cv2.polylines(img, [pts], True, edge, 1, cv2.LINE_AA)

    def _label_texture(self, t, size: int) -> np.ndarray:
        """目标的分割标签贴图。只有指针表分到三类，其余整块算一类。

        指示灯与开关把手不做像素级细分：它们的读数靠颜色和朝向，
        不靠亚像素的边界——为它们标掩膜是白花力气。
        """
        p = t.priors
        if str(p.get("kind") or "") == "POINTER_GAUGE":
            return gauges.render_pointer_gauge_mask(
                size, value=float(t.true_value), range_min=float(p["range_min"]),
                range_max=float(p["range_max"]), sweep_deg=float(p["sweep_deg"]),
                zero_offset_deg=float(p["zero_offset_deg"]),
                major_ticks=int(p["major_ticks"]))
        return np.full((size, size), gauges.SEG_LABELS["face"], np.uint8)

    @staticmethod
    def _warp_texture(img: np.ndarray, uv: np.ndarray, tex: np.ndarray,
                      *, label: bool = False) -> None:
        """把正方形贴图透视映射到四边形 uv（左上/右上/右下/左下）。

        用 warpPerspective 而不是简单缩放，这样相机斜看表盘时图上是**椭圆**，
        读数算法必须真的做透视校正（方案书 §6.1.4），不能假设正圆。

        `label=True` 时贴的是分割标签而不是颜色：**必须用最近邻插值、必须
        硬边**。线性插值会在"针"和"面"的交界处插出 1.5 这种不存在的类别，
        抗锯齿边缘同理——训练时这些像素会变成成片的错标，而且分布在最关键的
        位置（针的边缘正是决定角度的地方）。
        """
        h, w = tex.shape[:2]
        src = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
        dst = np.float32(uv)
        # 退化四边形（几乎看不到）直接跳过
        area = cv2.contourArea(dst.astype(np.float32))
        if area < 4.0:
            return
        M = cv2.getPerspectiveTransform(src, dst)
        H, W = img.shape[:2]
        x0 = max(0, int(np.floor(dst[:, 0].min())) - 2)
        y0 = max(0, int(np.floor(dst[:, 1].min())) - 2)
        x1 = min(W, int(np.ceil(dst[:, 0].max())) + 2)
        y1 = min(H, int(np.ceil(dst[:, 1].max())) + 2)
        if x1 <= x0 or y1 <= y0:
            return
        # 只在包围盒内做变换，整幅 warp 太慢
        T = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], np.float64)
        patch = cv2.warpPerspective(
            tex, T @ M, (x1 - x0, y1 - y0),
            flags=cv2.INTER_NEAREST if label else cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_TRANSPARENT,
            dst=img[y0:y1, x0:x1].copy())
        mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillConvexPoly(mask, (np.round(dst).astype(np.int32) - [x0, y0]), 255,
                           cv2.LINE_8 if label else cv2.LINE_AA)
        if label:
            sel = mask > 0
            roi = img[y0:y1, x0:x1]
            roi[sel] = patch[sel]
            return
        m3 = (mask[..., None].astype(np.float32) / 255.0)
        roi = img[y0:y1, x0:x1].astype(np.float32)
        img[y0:y1, x0:x1] = np.clip(patch.astype(np.float32) * m3 + roi * (1 - m3),
                                    0, 255).astype(np.uint8)

    # ------------------------------------------------------------ 背景
    def _draw_room(self, img: np.ndarray, cam: PinholeCamera) -> None:
        rm = self.world.room
        L = float(rm.get("length_m", 18.0))
        Wd = float(rm.get("width_m", 8.0))
        H = float(rm.get("height_m", 3.2))
        x0, x1 = -2.0, L
        y0, y1 = -Wd / 2.0, Wd / 2.0

        def quad(pts, color, edge=None):
            uv = self._project_polygon(cam, pts)
            if uv is not None:
                self._fill_quad(img, uv, color, edge)

        def seg(a, b, color, w=1):
            uv = self._project_polygon(cam, [a, b, b, a])
            if uv is not None and len(uv) >= 2:
                cv2.line(img, tuple(np.round(uv[0]).astype(int)),
                         tuple(np.round(uv[1]).astype(int)), color, w, cv2.LINE_AA)

        quad([(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)], _FLOOR)
        quad([(x0, y0, H), (x1, y0, H), (x1, y1, H), (x0, y1, H)], _CEIL)
        quad([(x1, y0, 0), (x1, y1, 0), (x1, y1, H), (x1, y0, H)], _WALL, _CABINET_EDGE)
        quad([(x0, y1, 0), (x1, y1, 0), (x1, y1, H), (x0, y1, H)], _WALL, _CABINET_EDGE)
        quad([(x0, y0, 0), (x1, y0, 0), (x1, y0, H), (x0, y0, H)], _WALL, _CABINET_EDGE)

        for gx in range(int(x0), int(x1) + 1, 2):
            seg((gx, y0, 0.0), (gx, y1, 0.0), (66, 66, 70))

        # 顶部灯具，同时也是表盘玻璃高光的来源
        for src in self.world.lighting.get("specular", {}).get("sources", []):
            sx, sy, sz = float(src["x_m"]), float(src["y_m"]), float(src["z_m"])
            quad([(sx - 0.5, sy - 0.2, sz - 0.02), (sx + 0.5, sy - 0.2, sz - 0.02),
                  (sx + 0.5, sy + 0.2, sz - 0.02), (sx - 0.5, sy + 0.2, sz - 0.02)],
                 (238, 240, 244))

        # 柜列。位置从 scene.cabinet_rows 读，不写死——柜面 y 与过道 y 之差
        # 就是观测距离，这个数是标定算例的来源，必须与配置同源。
        rows = self.world.cabinet_rows or [{"y_m": 1.82, "depth_m": 0.62}]
        for row in rows:
            yf = float(row.get("y_m", 1.82))
            depth = float(row.get("depth_m", 0.62))
            yb = yf + depth * (1.0 if yf < 0 else -1.0)
            for k, sx in enumerate(range(int(x0) + 1, int(x1), 3)):
                a, b = float(sx), float(sx) + 2.6
                shade = 1.0 if (k % 2 == 0) else 0.93     # 柜门交替明暗
                face = tuple(int(c * shade) for c in _CABINET)
                quad([(a, yf, 0.0), (b, yf, 0.0), (b, yf, 2.0), (a, yf, 2.0)],
                     face, _CABINET_EDGE)
                quad([(a, yf, 2.0), (b, yf, 2.0), (b, yb, 2.0), (a, yb, 2.0)],
                     tuple(int(c * 0.80) for c in _CABINET), _CABINET_EDGE)
                # 柜面细节：分格、通风百叶、铭牌、门把手。不只是好看——百叶的
                # 横线和铭牌边框是检测器与椭圆拟合会遇到的真实干扰。
                seg((a + 1.3, yf, 0.1), (a + 1.3, yf, 1.9), _CABINET_EDGE)
                seg((a, yf, 1.55), (b, yf, 1.55), _CABINET_EDGE)
                seg((a, yf, 0.45), (b, yf, 0.45), _CABINET_EDGE)
                for lv in range(6):
                    zl = 0.60 + lv * 0.045
                    seg((a + 0.25, yf, zl), (a + 1.05, yf, zl),
                        tuple(int(c * 0.72) for c in _CABINET))
                    seg((a + 1.55, yf, zl), (a + 2.35, yf, zl),
                        tuple(int(c * 0.72) for c in _CABINET))
                quad([(a + 0.30, yf, 1.72), (a + 0.95, yf, 1.72),
                      (a + 0.95, yf, 1.86), (a + 0.30, yf, 1.86)],
                     (206, 204, 198), _CABINET_EDGE)
                quad([(a + 1.22, yf, 0.95), (a + 1.38, yf, 0.95),
                      (a + 1.38, yf, 1.15), (a + 1.22, yf, 1.15)], (78, 78, 82))

    # ------------------------------------------------------------ 主流程
    def render(self, *, pose_xy_yaw: tuple[float, float, float],
               pan_deg: float, tilt_deg: float, zoom: float,
               speed_mps: float | None = None, want_mask: bool = False,
               ) -> tuple:
        """渲染一帧。

        返回 (图像, 目标元数据列表)。元数据里含 bbox、距离、像素密度等，
        **只给桩内部与测试用**，感知节点拿不到（camera 驱动只返回 Frame）。

        `want_mask=True` 时多返回一张分割标签图，用同一套 uv、同一次透视
        变换生成——**这是合成数据集的立足点**。像素级标注在真实场景里是最贵
        的一种（一块表盘要人描十几分钟），在这里它是渲染的免费副产品，而且
        天生与图像逐像素对齐，不需要任何配准。

        掩膜里不做 4K 裁剪的降采样与后处理（模糊、噪声、光照）：那些是
        **成像**的失真，标签不该跟着失真。
        """
        o = self.o
        x, y, yaw = pose_xy_yaw
        z = float(np.clip(zoom, 1.0, 8.0))
        hfov = hfov_at_zoom(o.hfov_at_1x_deg, z)

        # 4K 裁剪仿真：按有效像素比折算渲染分辨率，之后再放大回去
        k = (stub_effective_pixel_ratio(z, o.source_width, o.width)
             if o.simulate_4k_crop else 1.0)
        rw = max(160, int(round(o.width * k)))
        rh = max(90, int(round(o.height * k)))

        cam = PinholeCamera(rw, rh, hfov, (x, y, self.world.camera_height_m),
                            yaw, pan_deg, tilt_deg)
        img = np.full((rh, rw, 3), 30, np.uint8)
        self._draw_room(img, cam)

        # 目标按距离由远及近绘制
        vis = self.world.visible(cam, margin_px=max(40.0, rw * 0.05))
        vis.sort(key=lambda tv: -self.world.distance_to(tv[0], cam.origin))

        # 标签画布与渲染分辨率同尺寸，最后与图像一起缩放到输出分辨率
        lab = np.zeros((rh, rw), np.uint8) if want_mask else None
        meta: list[dict] = []
        for t, uv in vis:
            d = self.world.distance_to(t, cam.origin)
            want = int(max(24.0, abs(uv[:, 0].max() - uv[:, 0].min()) * 1.4))
            tex = self._texture(t, want)
            cos_face = t.facing_cosine(cam.origin)
            # 表盘玻璃高光：正对灯具且正对相机时最强
            glare = self._specular_strength(t, cam, cos_face)
            if glare > 1e-3 and str(t.priors.get("kind")) == "POINTER_GAUGE":
                tex = gauges.add_glass_glare(tex, strength=glare, rng=self.rng)
            self._warp_texture(img, uv, tex)
            if lab is not None:
                self._warp_texture(lab, uv, self._label_texture(t, tex.shape[0]),
                                   label=True)

            x1, y1 = float(uv[:, 0].min()), float(uv[:, 1].min())
            x2, y2 = float(uv[:, 0].max()), float(uv[:, 1].max())
            meta.append({
                "target_id": t.id,
                "defect_class": t.defect_class,
                "bbox": [x1 / k, y1 / k, x2 / k, y2 / k],   # 还原到输出分辨率
                "distance_m": d,
                "facing_cos": cos_face,
                "glare": glare,
                "target_size_m": t.diameter_m,
                "anomalous": t.anomalous,
                "waypoint": t.waypoint,
            })

        if k < 0.999:                       # 上采样：信息损失在这一步发生
            img = cv2.resize(img, (o.width, o.height), interpolation=cv2.INTER_LINEAR)
        elif (rw, rh) != (o.width, o.height):
            img = cv2.resize(img, (o.width, o.height), interpolation=cv2.INTER_AREA)

        img = self._post(img, speed_mps if speed_mps is not None else o.speed_mps, z)

        if o.draw_debug:
            self._annotate(img, meta, z)
        if lab is not None:
            if (lab.shape[1], lab.shape[0]) != (o.width, o.height):
                lab = cv2.resize(lab, (o.width, o.height),
                                 interpolation=cv2.INTER_NEAREST)
            return img, meta, lab
        return img, meta

    # ------------------------------------------------------------ 后处理
    def _specular_strength(self, t: Target, cam: PinholeCamera,
                           cos_face: float) -> float:
        """镜面高光强度。灯在顶部，反射角接近观察角时最强。"""
        lig = self.world.lighting.get("specular", {})
        if not lig.get("enabled", False):
            return 0.0
        best = 0.0
        strength = float(lig.get("strength", 0.85))
        width = math.radians(float(lig.get("angular_width_deg", 12.0)))
        n = np.array([math.cos(math.radians(t.facing_deg)),
                      math.sin(math.radians(t.facing_deg)), 0.0])
        v = cam.origin - t.position
        nv = np.linalg.norm(v)
        if nv < 1e-6:
            return 0.0
        v = v / nv
        for src in lig.get("sources", []):
            s = np.array([float(src["x_m"]), float(src["y_m"]), float(src["z_m"])])
            li = s - t.position
            nl = np.linalg.norm(li)
            if nl < 1e-6:
                continue
            li = li / nl
            refl = 2.0 * np.dot(n, li) * n - li          # 反射向量
            ang = math.acos(float(np.clip(np.dot(refl, v), -1.0, 1.0)))
            best = max(best, math.exp(-(ang / max(1e-6, width)) ** 2))
        return float(np.clip(best * strength * max(0.0, cos_face), 0.0, 1.0))

    def _gain_map(self, h: int, w: int) -> np.ndarray:
        """低照度压暗 × 暗角，两者都只依赖画幅，缓存复用。

        每帧重算 np.mgrid 与全幅浮点乘法会吃掉 90 % 的渲染时间。
        """
        lux = float(self.world.lighting.get("ambient_lux", 200.0))
        vig = float(self.world.noise.get("vignette", 0.0))
        key = (h, w, round(lux, 3), round(vig, 4), round(self.o.exposure_gain, 3))
        cached = getattr(self, "_gm_key", None)
        if cached == key:
            return self._gm
        g = float(np.clip(lux / 300.0, 0.35, 1.0)) * float(self.o.exposure_gain)
        m = np.full((h, w), g, np.float32)
        if vig > 1e-3:
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
            m *= (1.0 - vig * np.clip(r / 1.42, 0, 1) ** 2).astype(np.float32)
        self._gm_key, self._gm = key, cv2.merge([m, m, m])
        return self._gm

    def _post(self, img: np.ndarray, speed_mps: float, zoom: float) -> np.ndarray:
        n = self.world.noise
        h, w = img.shape[:2]

        # 运动模糊在 uint8 上做，cv2 的实现比浮点快得多
        blur_px = float(n.get("motion_blur_gain", 0.9)) * abs(speed_mps) * zoom
        if blur_px > 0.6:
            ksz = int(max(3, round(blur_px)) // 2 * 2 + 1)
            kern = np.zeros((ksz, ksz), np.float32)
            kern[ksz // 2, :] = 1.0 / ksz
            img = cv2.filter2D(img, -1, kern)

        out = cv2.multiply(img, self._gain_map(h, w), dtype=cv2.CV_32F)

        # 低照度要提增益，噪声随之抬升（方案书 §4.3.1）
        sigma = float(n.get("gaussian_sigma", 3.0))
        if sigma > 0:
            lux = float(self.world.lighting.get("ambient_lux", 200.0))
            gain = 1.0 + 1.6 * max(0.0, 1.0 - lux / 300.0)
            noise = np.empty((h, w, 3), np.float32)
            cv2.randn(noise, 0.0, sigma * gain)
            cv2.add(out, noise, dst=out)

        return cv2.convertScaleAbs(out)

    def _annotate(self, img: np.ndarray, meta: list[dict], zoom: float) -> None:
        from patrol.scene.optics import pixel_density
        for m in meta:
            x1, y1, x2, y2 = [int(round(v)) for v in m["bbox"]]
            p = pixel_density(self.o.width, m["target_size_m"], zoom,
                              m["distance_m"], self.o.hfov_at_1x_deg)
            ok = p >= 120.0
            col = (110, 210, 110) if ok else (80, 140, 240)
            cv2.rectangle(img, (x1, y1), (x2, y2), col, 2)
            cv2.putText(img, "%s  p=%.0fpx  d=%.2fm" % (m["defect_class"], p, m["distance_m"]),
                        (x1, max(16, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 2, cv2.LINE_AA)
