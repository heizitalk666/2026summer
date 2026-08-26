"""指示灯与开关位置识别。方案书 §2.1 第二类被测量。

这两类都是**离散状态量**，用正确率衡量而不是精度：指示灯 ≥98 %、
开关分合位 ≥99 %（方案书表 2-2）。开关分合位是差异清单 A2 用来替换 OIL_LEAK
的那一类——渗漏油没有公开标注数据，而它有。

关于伽马校正（方案书 §4.2 的提醒）：伽马是为人眼观感设计的非线性映射，会
破坏像素值与光通量之间的线性关系。用于指示灯亮度判定时应从伽马校正之前的
线性域取数，或对已校正的数据做反伽马还原，否则亮度阈值会随环境光漂移。
这里用 to_linear() 做反伽马，再判亮度。
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

#: 指示灯色相区间（OpenCV HSV，H 为 0–179）
HUE_BANDS = {
    "RED": ((0, 10), (170, 179)),
    "YELLOW": ((16, 35),),
    "GREEN": ((36, 85),),
    "BLUE": ((86, 130),),
}
SRGB_GAMMA = 2.2


@dataclass
class StateReading:
    ok: bool
    value: str | None = None
    confidence: float = 0.0
    fail_reason: str | None = None
    detail: dict | None = None


def to_linear(bgr: np.ndarray) -> np.ndarray:
    """反伽马，回到与光通量成正比的线性域。"""
    x = bgr.astype(np.float32) / 255.0
    return np.power(x, SRGB_GAMMA)


def _core(img: np.ndarray, bbox, shrink: float = 0.30) -> np.ndarray | None:
    """取检测框的中心区域，避开边框与背景。"""
    h, w = img.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in bbox]
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    hw, hh = (x2 - x1) * (1.0 - shrink) / 2.0, (y2 - y1) * (1.0 - shrink) / 2.0
    X1, Y1 = max(0, int(cx - hw)), max(0, int(cy - hh))
    X2, Y2 = min(w, int(cx + hw) + 1), min(h, int(cy + hh) + 1)
    if X2 - X1 < 2 or Y2 - Y1 < 2:
        return None
    return img[Y1:Y2, X1:X2]


def read_indicator_light(img: np.ndarray, bbox, priors: dict | None = None
                         ) -> StateReading:
    """指示灯：用颜色和亮灭编码运行/停止/故障/储能状态。

    灭灯的判据用线性域亮度而非 HSV 的 V：V 是伽马域的量，环境光一变阈值就漂。
    """
    roi = _core(img, bbox)
    if roi is None or roi.size < 12:
        return StateReading(False, fail_reason="ROI 太小")

    lin = to_linear(roi)
    lum = float(np.mean(0.0722 * lin[..., 0] + 0.7152 * lin[..., 1]
                        + 0.2126 * lin[..., 2]))     # BGR 顺序
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    # 只统计既亮又有色的像素，灯座与阴影不算
    m = (s > 70) & (v > 90)
    if int(np.count_nonzero(m)) < max(4, int(0.06 * m.size)) or lum < 0.02:
        return StateReading(True, value="OFF",
                            confidence=float(np.clip(1.0 - lum * 8.0, 0.3, 1.0)),
                            detail={"luminance_linear": round(lum, 5)})

    hs = h[m]
    scores = {}
    for name, bands in HUE_BANDS.items():
        cnt = 0
        for lo, hi in bands:
            cnt += int(np.count_nonzero((hs >= lo) & (hs <= hi)))
        scores[name] = cnt / float(hs.size)
    best = max(scores, key=scores.get)
    top = scores[best]
    second = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0.0
    # 置信度看第一名与第二名的差距，而不只是第一名的绝对占比
    conf = float(np.clip(0.5 * top + 0.5 * (top - second), 0.0, 1.0))
    if top < 0.35:
        return StateReading(False, confidence=conf, fail_reason="色相分布不明确",
                            detail={"scores": scores})
    return StateReading(True, value=best, confidence=conf,
                        detail={"scores": {k: round(v, 3) for k, v in scores.items()},
                                "luminance_linear": round(lum, 5)})
