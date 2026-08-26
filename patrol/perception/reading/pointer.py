"""指针式仪表读数。方案书 §6.1.1 / §6.1.4 / §5.3.2。

**这是全项目的测量核心。**算法只拿到一张图和一个检测框，必须真的从像素里
把指针转角解算出来。场景真值在 scene/world.py 里，本模块**永远不会去读它**
（见 scene/gauges.py 的纪律说明）——否则精度指标全是假的。

处理链：

    裁剪 ROI → 定位表盘（椭圆拟合）→ 透视校正为正圆
    → 提取指针（连通域，取"触及圆心且径向跨度最大"的那个）
    → 主轴方向（PCA）→ 消除 180° 歧义 → 标度变换

三个关键设计：

1. **靠"触及圆心"筛掉刻度与数字。**刻度在外圈 r>0.76R，数字是孤立块，
   只有指针从圆心一路伸出去。用连通域 + 触心判据比"找最长暗射线"稳得多，
   后者会被数字标签和配重尾巴带偏。

2. **靠径向跨度消除 180° 歧义。**指针有配重尾巴，主轴方向有两个候选，
   取质心离圆心更远的那一端。

3. **b/a < 0.85 就报告视角过斜。**方案书 §6.1.4：此时补偿后残余误差仍不
   可忽略，应由状态机下发云台角度调整重新拍摄——用控制手段替代软件补偿，
   这正是本课题"主动测量"的体现。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from patrol.perception.reading.scale import (angle_to_value, in_normal_band,
                                             wrap180)

WORK = 256                 # 统一工作分辨率，让阈值与形态学核尺寸有确定含义
MIN_AXIS_RATIO = 0.85      # b/a 低于此值判为视角过斜（方案书 §6.1.4）


@dataclass
class PointerReading:
    """一次读数的完整结果，含中间量便于排查与可视化。"""

    ok: bool
    angle_deg: float | None = None        # 指针转角，自 12 点方向顺时针
    value: float | None = None
    confidence: float = 0.0
    axis_ratio: float = 1.0               # 椭圆短轴/长轴，1 = 正对
    center_px: tuple[float, float] | None = None    # 原图坐标系
    radius_px: float = 0.0
    tip_px: tuple[float, float] | None = None
    glare_ratio: float = 0.0
    fail_reason: str | None = None
    debug: dict | None = None


def _prep(img: np.ndarray, bbox, margin: float = 0.18
          ) -> tuple[np.ndarray, float, float, float] | None:
    """裁剪 ROI 并缩放到工作分辨率。返回 (patch, scale, x0, y0)。"""
    h, w = img.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in bbox]
    bw, bh = x2 - x1, y2 - y1
    if bw < 6 or bh < 6:
        return None
    mx, my = bw * margin, bh * margin
    X1 = max(0, int(math.floor(x1 - mx)))
    Y1 = max(0, int(math.floor(y1 - my)))
    X2 = min(w, int(math.ceil(x2 + mx)))
    Y2 = min(h, int(math.ceil(y2 + my)))
    if X2 - X1 < 6 or Y2 - Y1 < 6:
        return None
    patch = img[Y1:Y2, X1:X2]
    side = max(patch.shape[0], patch.shape[1])
    scale = WORK / float(side)
    patch = cv2.resize(patch, (max(8, int(round(patch.shape[1] * scale))),
                               max(8, int(round(patch.shape[0] * scale)))),
                       interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    return patch, scale, float(X1), float(Y1)


def _find_dial(gray: np.ndarray) -> tuple[tuple, float] | None:
    """定位表盘，返回 (椭圆, 短长轴比)。

    表盘在图上有两条同心边界可用：暗色表圈的外缘，和白色表面的内缘。哪一条
    更好用取决于背景比表圈亮还是暗——配电柜是浅灰的，表圈是深色的，但换个
    场景就反过来了。所以**两种极性都试**，按圆度打分挑最好的那个候选，
    而不是假设某一种。

    对轮廓拟合椭圆而不是假设正圆：相机斜看表盘时它在图上本来就是椭圆，
    短长轴比正是判断视角是否过斜的依据（方案书 §6.1.4）。
    """
    H, W = gray.shape[:2]
    area_all = float(H * W)
    g = cv2.GaussianBlur(gray, (5, 5), 0)
    _, bright = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    k = np.ones((5, 5), np.uint8)
    cands: list[tuple] = []
    for mask in (cv2.morphologyEx(bright, cv2.MORPH_CLOSE, k),
                 cv2.morphologyEx(cv2.bitwise_not(bright), cv2.MORPH_CLOSE, k)):
        cnts, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        cands.extend(cnts)

    best, best_score = None, -1.0
    for c in cands:
        if len(c) < 5:
            continue
        area = cv2.contourArea(c)
        # 太小是噪块，太大是整幅画面的外框
        if not (0.03 * area_all <= area <= 0.80 * area_all):
            continue
        (cx, cy), (MA, ma), ang = cv2.fitEllipse(c)
        a, b = max(MA, ma), min(MA, ma)
        if a < 12 or b < 6:
            continue
        fill = area / max(1e-6, math.pi * a * b / 4.0)
        if fill < 0.80:                       # 轮廓要真的填满椭圆，排除弧段与文字
            continue
        d = math.hypot(cx - W / 2.0, cy - H / 2.0) / max(W, H)
        score = area * fill * (1.0 - 0.8 * d)
        if score > best_score:
            best_score, best = score, ((cx, cy), (MA, ma), ang)

    if best is None:
        # 兜底：霍夫圆。表盘被部分遮挡时轮廓不闭合，霍夫仍可能找到
        circles = cv2.HoughCircles(g, cv2.HOUGH_GRADIENT, dp=1.2,
                                   minDist=max(8, W // 3), param1=110, param2=45,
                                   minRadius=int(0.12 * min(H, W)),
                                   maxRadius=int(0.52 * min(H, W)))
        if circles is None:
            return None
        cx, cy, r = circles[0][0]
        best = ((float(cx), float(cy)), (2.0 * float(r), 2.0 * float(r)), 0.0)

    MA, ma = best[1]
    return best, float(min(MA, ma) / max(1e-6, max(MA, ma)))


def _rectify(patch: np.ndarray, ellipse) -> tuple[np.ndarray, np.ndarray, float]:
    """把椭圆表盘拉回正圆。方案书 §6.1.4 的透视校正。

    构造仿射变换：绕椭圆中心旋转 -φ、沿短轴方向拉伸 a/b、再旋转回来。
    返回 (校正后图, 变换矩阵, 校正后半径)。
    """
    (cx, cy), (MA, ma), phi = ellipse
    a, b = max(MA, ma) / 2.0, min(MA, ma) / 2.0
    # fitEllipse 的角度是长轴与 x 轴夹角；MA 为第一个轴长
    long_is_first = MA >= ma
    theta = math.radians(phi if long_is_first else phi + 90.0)
    c, s = math.cos(theta), math.sin(theta)
    R = np.array([[c, s], [-s, c]])                 # 旋到长轴沿 x
    S = np.array([[1.0, 0.0], [0.0, a / max(1e-6, b)]])   # 沿短轴拉伸
    Mlin = R.T @ S @ R
    t = np.array([cx, cy]) - Mlin @ np.array([cx, cy])
    Maff = np.hstack([Mlin, t.reshape(2, 1)]).astype(np.float32)
    out = cv2.warpAffine(patch, Maff, (patch.shape[1], patch.shape[0]),
                         flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return out, Maff, float(a)


#: 径向采样带，相对表盘外缘半径 R。
#: 下限 0.28R 越过轴帽与配重尾巴（尾巴只到 0.18R），同时避开靠近轴帽处
#: 指针张开角过大的一段。上限 0.70R 略微探进刻度带（刻度自 0.68R 起）是
#: 有意的：逐环取角质心再取**中位数**，被刻度污染的那一两环会成为离群点被
#: 挡掉，而更长的力臂换来的精度提升是实打实的（实测线性度 0.42→0.34 %FS）。
BAND_LO, BAND_HI = 0.28, 0.70
N_THETA = 1440                # 极坐标展开的角度分辨率，0.25°/行
N_RADIUS = 128
REFINE_WIN_DEG = 6.0   # 细化窗半宽，度


def _needle_angle(gray_rect: np.ndarray, center: tuple[float, float],
                  radius: float) -> tuple[float, float, tuple[float, float]] | None:
    """在校正后的正圆表盘上找指针。返回 (角度, 置信度, 尖端坐标)。

    做法是**极坐标展开 + 径向覆盖率扫描**：

        warpPolar 把表盘展成 (角度 × 半径) 的矩形，指针在这张图上是一条
        贯穿整个半径带的竖直暗条；刻度只出现在带外，数字是孤立块，都给不出
        贯穿式的覆盖。取每一行（每个角度）在带内的暗覆盖率，峰值就是指针。

    两个关键选择：

    1. **必须用 warpPolar 的双线性插值，不能自己按整数像素取样。**手写射线
       采样时相邻 0.5° 的射线在半径 45 px 处只差 0.39 px，会采到同一批像素，
       覆盖率曲线被量化成台阶，亚度级质心就被台阶花纹带偏——实测这一项能
       贡献 2° 以上的随机误差，直接吃掉 1.35° 的全部预算。

    2. **覆盖率用软阈值而不是硬二值。**硬二值同样会把亚像素信息丢掉。
       软斜坡让指针边缘的部分覆盖如实反映出来，质心才是无偏的。

    角度自 12 点方向顺时针为正，与 scene/gauges 的约定一致。
    """
    cx, cy = center
    H, W = gray_rect.shape[:2]
    g = cv2.GaussianBlur(gray_rect, (3, 3), 0)

    r_out = float(BAND_HI) * radius
    if r_out < 6.0:
        return None
    # 极坐标展开：行 = 角度（自 +x 轴起，图像 y 向下故视觉上顺时针），列 = 半径
    polar = cv2.warpPolar(g, (N_RADIUS, N_THETA), (float(cx), float(cy)),
                          r_out, cv2.WARP_POLAR_LINEAR + cv2.INTER_LINEAR)
    c_lo = int(round(N_RADIUS * BAND_LO / BAND_HI))
    band = polar[:, c_lo:].astype(np.float32)
    if band.size < 64:
        return None

    # 环内 Otsu 定阈值：表盘内部暗像素通常不到面积的 10 %，取固定分位会落在
    # 接近白面的值上，把抗锯齿晕圈整片吞进来。
    thr, _ = cv2.threshold(band.astype(np.uint8).reshape(-1, 1), 0, 255,
                           cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = float(thr)
    soft = np.clip((thr - band) / max(1e-6, 0.35 * thr), 0.0, 1.0)
    cover = soft.mean(axis=1)                      # 每个角度的暗覆盖率

    # 角度是环量，平滑时要环绕
    k = np.ones(5, np.float32) / 5.0
    cover = np.convolve(np.r_[cover[-4:], cover, cover[:4]], k, mode="same")[4:-4]

    peak = int(np.argmax(cover))
    if float(cover[peak]) < 0.45:
        return None

    # ---- 亚度级细化：逐半径环各算一次角质心，再取中位数 ----
    #
    # 不能把覆盖率沿半径求和再取质心：指针在图上是等宽的实体，靠近轴帽处
    # 张开的角度大（0.25R 处约 ±3.8°），靠近尖端处窄（0.60R 处约 ±1.6°），
    # 求和会让内圈的宽裙边主导质心，把结果拽偏。
    #
    # 逐环算则每一环都是一条对称的暗度剖面，其角质心就是该环上指针的中线；
    # 各环给出的角度理应一致，取中位数既平均掉噪声又抗住个别被数字标签
    # 污染的环。
    step = 360.0 / N_THETA
    half = max(2, int(round(REFINE_WIN_DEG / step)))
    idx = (np.arange(peak - half, peak + half + 1)) % N_THETA
    rel = (np.arange(-half, half + 1)).astype(np.float64) * step
    win = soft[idx, :]                                   # (2·half+1, n_r)
    ring_w = win.sum(axis=0)
    good = ring_w > 0.35 * float(np.median(ring_w[ring_w > 0])) if np.any(ring_w > 0) else None
    if good is None or int(np.count_nonzero(good)) < 4:
        off = 0.0
    else:
        cen = (rel[:, None] * win[:, good]).sum(axis=0) / ring_w[good]
        off = float(np.median(cen))
    ang_ccw_x = (peak * step + off) % 360.0              # 自 +x 轴，图像系

    # 图像系 (+x 起、y 向下) → 表盘系 (12 点起、顺时针为正)
    ang = wrap180(ang_ccw_x + 90.0)

    side = float(np.median(cover))
    sharp = float(np.clip((float(cover[peak]) - side) / max(1e-6, 1.0 - side), 0.0, 1.0))
    conf = float(np.clip(0.45 * float(cover[peak]) + 0.55 * sharp, 0.0, 1.0))

    a = math.radians(ang - 90.0)
    tip = (float(cx + math.cos(a) * BAND_HI * radius),
           float(cy + math.sin(a) * BAND_HI * radius))
    return ang, conf, tip


def read_pointer_gauge(img: np.ndarray, bbox, priors: dict,
                       *, want_debug: bool = False) -> PointerReading:
    """从图像与检测框解算一块指针表的读数。

    priors 是标定阶段录入的先验（量程、扫过角、零位偏置、正常区间），
    **不含当前读数**。
    """
    prep = _prep(img, bbox)
    if prep is None:
        return PointerReading(False, fail_reason="ROI 太小")
    patch, scale, x0, y0 = prep
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch

    # 高光占比：玻璃反光盖住刻度时读数不可信，这正是三视角要规避的
    glare = float(np.mean(gray >= 250))

    found = _find_dial(gray)
    if found is None:
        return PointerReading(False, glare_ratio=glare, fail_reason="未找到表盘轮廓")
    ellipse, axis_ratio = found

    if axis_ratio >= 0.98:
        # 近似正圆时跳过仿射重采样：warpAffine 的双线性插值会引入一点模糊，
        # 而此时透视畸变本来就可忽略，重采样纯属亏本。
        rect = patch
        Maff = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], np.float32)
        radius = float(max(ellipse[1]) / 2.0)
    else:
        rect, Maff, radius = _rectify(patch, ellipse)
    gray_rect = cv2.cvtColor(rect, cv2.COLOR_BGR2GRAY) if rect.ndim == 3 else rect
    (cx, cy) = ellipse[0]

    res = _needle_angle(gray_rect, (cx, cy), radius)
    if res is None:
        return PointerReading(False, axis_ratio=axis_ratio, glare_ratio=glare,
                              fail_reason="未提取到指针")
    ang, conf, tip = res

    value = angle_to_value(
        ang, range_min=float(priors.get("range_min", 0.0)),
        range_max=float(priors.get("range_max", 1.0)),
        sweep_deg=float(priors.get("sweep_deg", 270.0)),
        zero_offset_deg=float(priors.get("zero_offset_deg", -135.0)))

    # 视角过斜与强高光都要压低置信度，让上层有依据决定重拍
    if axis_ratio < MIN_AXIS_RATIO:
        conf *= 0.55
    if glare > 0.06:
        conf *= float(np.clip(1.0 - (glare - 0.06) * 6.0, 0.2, 1.0))

    inv = cv2.invertAffineTransform(Maff)
    tp = inv @ np.array([tip[0], tip[1], 1.0])
    cp = inv @ np.array([cx, cy, 1.0])

    dbg = None
    if want_debug:
        dbg = {"patch": patch, "rect": rect, "ellipse": ellipse,
               "radius": radius, "center_work": (cx, cy), "tip_work": tip,
               "scale": scale}
    return PointerReading(
        ok=True, angle_deg=round(float(ang), 4), value=round(float(value), 6),
        confidence=round(float(conf), 4), axis_ratio=round(float(axis_ratio), 4),
        center_px=(x0 + float(cp[0]) / scale, y0 + float(cp[1]) / scale),
        radius_px=float(radius) / scale * 2.0,
        tip_px=(x0 + float(tp[0]) / scale, y0 + float(tp[1]) / scale),
        glare_ratio=round(glare, 4), debug=dbg)


def reading_to_l2(reading: PointerReading, priors: dict) -> dict:
    """转成 DetectionEvent.detections[].l2_reading。"""
    return {
        "kind": "POINTER_GAUGE",
        "value": None if not reading.ok else float(reading.value),
        "unit": priors.get("unit"),
        "range_min": priors.get("range_min"),
        "range_max": priors.get("range_max"),
        "in_normal_band": (None if not reading.ok
                           else in_normal_band(reading.value, priors.get("normal_band"))),
        "reading_confidence": float(np.clip(reading.confidence, 0.0, 1.0)),
        "roi": [0.0, 0.0, 0.0, 0.0],
    }
