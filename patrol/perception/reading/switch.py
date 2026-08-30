"""开关把手分合位识别。

把手朝向编码分合位：竖直为合位（ON/CLOSED），水平为分位（OFF/OPEN）。
方案书表 2-2 要求正确率 ≥99 %——分合位误判后果严重，这是全系统正确率
要求最高的一项。

做法：在 ROI 内提取暗色长条（把手），用最小外接矩形取主轴方向，再按角度
分类。不做模板匹配，因为把手在不同柜型上外形差异大，而"细长暗条的朝向"
这个特征是稳定的。
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from patrol.perception.reading.indicator import StateReading, _core


def read_switch_position(img: np.ndarray, bbox, priors: dict | None = None
                         ) -> StateReading:
    roi = _core(img, bbox, shrink=0.12)
    if roi is None or roi.size < 64:
        return StateReading(False, fail_reason="ROI 太小")
    if roi.shape[0] < 12 or roi.shape[1] < 12:
        roi = cv2.resize(roi, (max(24, roi.shape[1] * 3), max(24, roi.shape[0] * 3)),
                         interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    # **多阈值搜索，而不是单用 Otsu。**把手比底座暗得多，但 Otsu 的分割点
    # 落在"暗物 vs 柜面"上，会把底座圆盘和把手并成一个近似方形的连通域，
    # 细长比掉到 1.2，朝向就无从谈起了。这里在若干个暗度分位上各试一次，
    # 取"能分离出最细长的中心目标"的那个——找细长暗条本来就是我们的目的。
    H, W = gray.shape[:2]
    cx0, cy0 = W / 2.0, H / 2.0
    otsu, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thresholds = [float(np.percentile(gray, q)) for q in (8, 14, 22, 32)] + [float(otsu)]

    best, best_score = None, -1.0
    for thr in thresholds:
        dark = (gray <= thr).astype(np.uint8) * 255
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area < max(12, int(0.008 * H * W)):
                continue
            ys, xs = np.nonzero(labels == i)
            pts = np.column_stack([xs, ys]).astype(np.float32)
            (_, _), (w_, h_), _ = cv2.minAreaRect(pts)
            elong = max(w_, h_) / max(1e-6, min(w_, h_))
            d = math.hypot(float(xs.mean()) - cx0, float(ys.mean()) - cy0) / max(W, H)
            # 把手是细长的、且过中心；铭牌与文字不满足
            score = elong * (1.0 - 1.4 * d) * min(1.0, area / (0.03 * H * W))
            if score > best_score:
                best_score, best = score, (pts, elong)
    if best is None:
        return StateReading(False, fail_reason="未找到细长把手")

    pts, elong = best
    centred = pts - pts.mean(axis=0)
    vt = np.linalg.svd(centred, full_matrices=False)[2]
    vx, vy = float(vt[0][0]), float(vt[0][1])
    ang = abs(math.degrees(math.atan2(vy, vx))) % 180.0     # 0=水平, 90=竖直
    is_vertical = 45.0 <= ang <= 135.0

    # 置信度由两件事决定：
    #   1. 朝向有多接近正交轴。0° 或 90° 时最可信，45° 附近说明把手朝向暧昧，
    #      分合位判不出来——这种情况必须让上层知道，因为分合位误判后果严重。
    #   2. 目标有多细长。把手是细长条，铭牌或阴影块不是。
    dev = min(abs(ang - 0.0), abs(ang - 90.0), abs(ang - 180.0))   # 0–45°
    conf = float(np.clip(1.0 - dev / 45.0, 0.0, 1.0))
    conf *= float(np.clip((elong - 1.5) / 2.0, 0.25, 1.0))

    return StateReading(True, value="CLOSED" if is_vertical else "OPEN",
                        confidence=round(conf, 4),
                        detail={"axis_deg": round(ang, 2), "elongation": round(elong, 2)})
