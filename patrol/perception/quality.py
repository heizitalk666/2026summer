"""图像质量评价：四项指标与质量总分 Q。

差异清单 A4：方案书 §6.4 的触发条件是"计算四项质量指标 → 加权得质量总分
Q < 0.75"，但**全文从未列出是哪四项**（只写了"像素密度等四项"）；ICD 的
DetectionEvent 里更是连 quality_score 字段都没有。任务书把"图像质量评价"
列为主要研究内容之一，这条不能就这么丢掉。

这里把四项显式定义下来：

| 指标                 | 量什么                       | 为什么要它                          |
|----------------------|------------------------------|-------------------------------------|
| ``pixel_density``    | 空间采样密度是否够读准       | 全项目的立论，120 px 判据           |
| ``blur``             | 清晰度／运动模糊             | 车没停稳或对焦没锁时读数不可信      |
| ``highlight``        | ROI 内过曝像素占比           | 玻璃反光盖住刻度，**A3 条件式辅视角靠它触发** |
| ``occlusion``        | 目标被遮挡或截出画面的比例   | 只拍到半块表读不出数                |

四项都归一化到 0–1（1 = 好），加权和即质量总分 Q。权重与阈值在
configs/system.yaml 的 perception.quality 下，评审时可调。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np

from patrol.scene.optics import pixel_density


@dataclass
class Quality:
    pixel_density_px: float
    pixel_density: float          # 归一化，1 = 达到 120 px 判据
    blur: float
    highlight: float
    occlusion: float
    score: float                  # 加权总分，即方案书的 Q

    def as_dict(self) -> dict:
        d = asdict(self)
        return {k: round(float(v), 4) for k, v in d.items()}


def _roi(img: np.ndarray, bbox) -> np.ndarray | None:
    h, w = img.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in bbox]
    X1, Y1 = max(0, x1), max(0, y1)
    X2, Y2 = min(w, x2 + 1), min(h, y2 + 1)
    if X2 - X1 < 3 or Y2 - Y1 < 3:
        return None
    return img[Y1:Y2, X1:X2]


def blur_score(img: np.ndarray, bbox) -> float:
    """清晰度。拉普拉斯方差归一化，1 = 清晰。

    方差按 ROI 的对比度归一，否则暗处的清晰目标会被误判为模糊。
    """
    roi = _roi(img, bbox)
    if roi is None:
        return 0.0
    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    lap = float(cv2.Laplacian(g.astype(np.float32), cv2.CV_32F).var())
    contrast = float(g.std()) + 1e-6
    norm = lap / (contrast * contrast)
    return float(np.clip(norm / 0.55, 0.0, 1.0))


def highlight_ratio(img: np.ndarray, bbox, *, sat_level: int = 248) -> float:
    """过曝像素占比。玻璃镜面反射是本场景最主要的光学干扰源（方案书 §4.3.1）。"""
    roi = _roi(img, bbox)
    if roi is None:
        return 1.0
    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    return float(np.mean(g >= sat_level))


def occlusion_ratio(img: np.ndarray, bbox) -> float:
    """目标被截出画面的比例。

    真正的遮挡（被别的物体挡住）需要深度信息才判得准，这里只量可靠的那部分：
    检测框超出画幅的面积占比。截断本身就足以让读数失效。
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in bbox]
    full = max(1e-6, (x2 - x1) * (y2 - y1))
    ix1, iy1 = max(0.0, x1), max(0.0, y1)
    ix2, iy2 = min(float(w), x2), min(float(h), y2)
    inside = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    return float(np.clip(1.0 - inside / full, 0.0, 1.0))


def evaluate(img: np.ndarray, bbox, *, target_size_m: float, zoom: float,
             distance_m: float, hfov_at_1x_deg: float, cfg_quality: dict
             ) -> Quality:
    """四项指标 + 加权总分。"""
    target_px = float(cfg_quality.get("pixel_density_target", 120.0))
    w = dict(cfg_quality.get("weights", {}))
    p = pixel_density(img.shape[1], target_size_m, zoom, distance_m, hfov_at_1x_deg)

    q_pd = float(np.clip(p / max(1e-6, target_px), 0.0, 1.0))
    q_blur = blur_score(img, bbox)
    hi = highlight_ratio(img, bbox)
    q_hi = float(np.clip(1.0 - hi / 0.15, 0.0, 1.0))     # 15 % 过曝即判为不可用
    oc = occlusion_ratio(img, bbox)
    q_oc = float(np.clip(1.0 - oc / 0.30, 0.0, 1.0))

    total_w = sum(float(v) for v in w.values()) or 1.0
    score = (float(w.get("pixel_density", 0.5)) * q_pd
             + float(w.get("blur", 0.2)) * q_blur
             + float(w.get("highlight", 0.2)) * q_hi
             + float(w.get("occlusion", 0.1)) * q_oc) / total_w

    return Quality(pixel_density_px=round(float(p), 2), pixel_density=q_pd,
                   blur=q_blur, highlight=q_hi, occlusion=q_oc,
                   score=float(np.clip(score, 0.0, 1.0)))
