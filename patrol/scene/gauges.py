"""表计的程序化绘制。

每个函数返回一张正方形 BGR 贴图，由 render.py 透视映射到目标所在的平面。

**纪律**：这里是"画"，按真值把指针画到该在的角度；
perception/reading/ 是"读"，只拿到像素，必须真的从图里把角度解算回来。
两边不允许共享任何中间量——真值只在 world.py 里保存一份，用于最后打分。
读数算法一旦读到真值，精度指标就全是假的，这条比任何测试都重要。

绘制上刻意保留了三样会给读数算法添麻烦的东西，因为真机上它们都存在：
指针有配重尾巴（不能简单取"最长的黑色射线"）、刻度盘有细分刻度（边缘检测
会多出干扰线）、表盘玻璃有环形高光（方案书 §4.3.1 列为本场景最主要的
光学干扰源）。
"""
from __future__ import annotations

import math

import cv2
import numpy as np

# BGR
_FACE = (242, 242, 238)
_BEZEL = (58, 58, 62)
_INK = (28, 28, 30)
_RED_ZONE = (60, 60, 205)
_GREEN_ZONE = (90, 165, 90)


def value_to_angle(value: float, range_min: float, range_max: float,
                   sweep_deg: float, zero_offset_deg: float) -> float:
    """标度变换的**逆变换**：工程量 → 指针转角（自 12 点方向顺时针，度）。

    正变换在 perception/reading/scale.py，两者必须互为逆运算，
    tests 里会往返验证。
    """
    span = float(range_max) - float(range_min)
    frac = 0.0 if abs(span) < 1e-12 else (float(value) - float(range_min)) / span
    frac = float(np.clip(frac, 0.0, 1.0))
    return float(zero_offset_deg) + frac * float(sweep_deg)


def _polar(cx: float, cy: float, r: float, angle_cw_from_up_deg: float
           ) -> tuple[int, int]:
    """表盘自身坐标系：角度自 12 点方向顺时针为正。"""
    a = math.radians(angle_cw_from_up_deg - 90.0)
    return int(round(cx + r * math.cos(a))), int(round(cy + r * math.sin(a)))


def render_pointer_gauge(
    size_px: int, *, value: float, range_min: float, range_max: float,
    sweep_deg: float = 270.0, zero_offset_deg: float = -135.0,
    major_ticks: int = 27, unit: str = "", normal_band: tuple | None = None,
    glass_glare: float = 0.0, rng: np.random.Generator | None = None,
) -> np.ndarray:
    """指针式仪表。size_px 是贴图边长，表盘直径占其 0.92。"""
    rng = rng or np.random.default_rng(0)
    s = int(size_px)
    img = np.full((s, s, 3), 236, np.uint8)
    cx = cy = (s - 1) / 2.0
    r_out = s * 0.46
    r_face = r_out * 0.90
    r_tick = r_face * 0.88
    r_tick_in = r_face * 0.76
    r_minor_in = r_face * 0.82

    cv2.circle(img, (int(cx), int(cy)), int(r_out), _BEZEL, -1, cv2.LINE_AA)
    cv2.circle(img, (int(cx), int(cy)), int(r_face), _FACE, -1, cv2.LINE_AA)

    # 正常区间用绿弧、超限段用红弧标出，指示读数是否落在带内
    if normal_band and len(normal_band) == 2:
        a0 = value_to_angle(normal_band[0], range_min, range_max, sweep_deg, zero_offset_deg)
        a1 = value_to_angle(normal_band[1], range_min, range_max, sweep_deg, zero_offset_deg)
        box = (int(cx), int(cy)), (int(r_face * 0.94), int(r_face * 0.94)), 0.0
        cv2.ellipse(img, box[0], box[1], -90.0, zero_offset_deg, a0, _RED_ZONE, max(2, s // 90), cv2.LINE_AA)
        cv2.ellipse(img, box[0], box[1], -90.0, a0, a1, _GREEN_ZONE, max(2, s // 90), cv2.LINE_AA)
        cv2.ellipse(img, box[0], box[1], -90.0, a1, zero_offset_deg + sweep_deg,
                    _RED_ZONE, max(2, s // 90), cv2.LINE_AA)

    n_major = max(2, int(major_ticks))
    for i in range(n_major):
        a = zero_offset_deg + sweep_deg * i / (n_major - 1)
        w = max(1, s // 110)
        cv2.line(img, _polar(cx, cy, r_tick, a), _polar(cx, cy, r_tick_in, a),
                 _INK, w, cv2.LINE_AA)
        # 细分刻度：给边缘检测添点干扰，真表盘上也有
        if i < n_major - 1:
            for k in (1, 2, 3, 4):
                am = a + sweep_deg / (n_major - 1) * k / 5.0
                cv2.line(img, _polar(cx, cy, r_tick, am),
                         _polar(cx, cy, r_minor_in, am), _INK, 1, cv2.LINE_AA)

    # 数字标签，每 1/4 量程一个
    if s >= 90:
        for i in range(5):
            frac = i / 4.0
            a = zero_offset_deg + sweep_deg * frac
            val = range_min + frac * (range_max - range_min)
            txt = ("%g" % round(val, 2))
            fs = s / 420.0
            tw, th = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)[0]
            px, py = _polar(cx, cy, r_tick_in * 0.80, a)
            cv2.putText(img, txt, (px - tw // 2, py + th // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, _INK, max(1, s // 200), cv2.LINE_AA)
        if unit:
            fs = s / 460.0
            tw = cv2.getTextSize(unit, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)[0][0]
            cv2.putText(img, unit, (int(cx - tw / 2), int(cy + r_face * 0.45)),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, _INK, max(1, s // 220), cv2.LINE_AA)

    # 指针：主体 + 配重尾巴。尾巴让"取最长黑色射线"这种偷懒解法失效
    ang = value_to_angle(value, range_min, range_max, sweep_deg, zero_offset_deg)
    r_needle = r_face * 0.80
    r_tail = r_face * 0.20
    tipx, tipy = _polar(cx, cy, r_needle, ang)
    tailx, taily = _polar(cx, cy, r_tail, ang + 180.0)
    w_needle = max(2, int(s * 0.016))
    cv2.line(img, (tailx, taily), (tipx, tipy), _INK, w_needle, cv2.LINE_AA)
    # 指针根部略粗，尖端收细
    cv2.circle(img, (int(cx), int(cy)), max(3, int(s * 0.035)), _BEZEL, -1, cv2.LINE_AA)
    cv2.circle(img, (int(cx), int(cy)), max(2, int(s * 0.022)), _INK, -1, cv2.LINE_AA)

    if glass_glare > 1e-3:
        img = add_glass_glare(img, strength=glass_glare, rng=rng)
    return img


def add_glass_glare(img: np.ndarray, *, strength: float,
                    rng: np.random.Generator | None = None) -> np.ndarray:
    """表盘玻璃的镜面高光。

    方案书 §4.3.1：顶部灯具在表盘玻璃上形成镜面高光，覆盖部分刻度，是本场景
    最主要的光学干扰源。抑制方法一是加偏振片（进光量减半），二是**利用本系统
    的主动能力改变云台角度重新拍摄，从几何上避开反射角**——后者正是 A3
    条件式三视角要做的事。
    """
    rng = rng or np.random.default_rng(0)
    s = img.shape[0]
    glare = np.zeros((s, s), np.float32)
    # 一条斜向的椭圆高光带，位置随机但偏上（灯在顶部）
    cx = int(s * (0.30 + 0.25 * rng.random()))
    cy = int(s * (0.22 + 0.18 * rng.random()))
    cv2.ellipse(glare, (cx, cy), (int(s * 0.34), int(s * 0.13)),
                float(rng.uniform(-40, 20)), 0, 360, 1.0, -1, cv2.LINE_AA)
    glare = cv2.GaussianBlur(glare, (0, 0), s * 0.05)
    glare = np.clip(glare * float(strength), 0.0, 1.0)[..., None]
    out = img.astype(np.float32)
    out = out * (1.0 - glare) + 255.0 * glare
    return np.clip(out, 0, 255).astype(np.uint8)


_LIGHT_BGR = {
    "RED": (60, 60, 235), "GREEN": (90, 220, 110),
    "YELLOW": (70, 210, 240), "BLUE": (235, 170, 70), "OFF": (70, 70, 74),
}


def render_indicator_light(size_px: int, *, color: str, on: bool = True
                           ) -> np.ndarray:
    """指示灯。用颜色和亮灭编码运行/停止/故障/储能状态。"""
    s = int(size_px)
    img = np.full((s, s, 3), 48, np.uint8)
    c = (s - 1) // 2
    cv2.rectangle(img, (0, 0), (s - 1, s - 1), (40, 40, 44), -1)
    cv2.circle(img, (c, c), int(s * 0.44), (30, 30, 34), -1, cv2.LINE_AA)   # 灯座
    base = _LIGHT_BGR.get(str(color).upper(), _LIGHT_BGR["OFF"])
    if not on:
        base = tuple(int(v * 0.28) for v in base)
    cv2.circle(img, (c, c), int(s * 0.36), base, -1, cv2.LINE_AA)
    if on:
        # 亮灯的辉光。注意伽马校正会破坏像素值与光通量的线性关系，
        # 判亮度阈值时要从线性域取数（方案书 §4.2 的提醒）。
        glow = np.zeros((s, s, 3), np.float32)
        cv2.circle(glow, (c, c), int(s * 0.34), tuple(float(v) for v in base), -1, cv2.LINE_AA)
        glow = cv2.GaussianBlur(glow, (0, 0), s * 0.10)
        img = np.clip(img.astype(np.float32) + glow * 0.65, 0, 255).astype(np.uint8)
        cv2.circle(img, (int(c - s * 0.10), int(c - s * 0.10)), max(1, int(s * 0.07)),
                   (255, 255, 255), -1, cv2.LINE_AA)   # 高光点
    return img


def render_switch_handle(size_px: int, *, position: str) -> np.ndarray:
    """隔离开关/断路器把手。分合位由把手朝向表示。

    A2 用它替换 ICD 原定的 OIL_LEAK：渗漏油没有公开标注数据（方案书
    §6.2.1），而开关分合位识别正确率 ≥99 % 是方案书表 2-2 的正式验收指标。
    """
    s = int(size_px)
    img = np.full((s, s, 3), 150, np.uint8)
    cv2.rectangle(img, (0, 0), (s - 1, s - 1), (152, 150, 146), -1)      # 柜面
    c = (s - 1) // 2
    cv2.circle(img, (c, c), int(s * 0.30), (96, 96, 100), -1, cv2.LINE_AA)  # 底座
    cv2.circle(img, (c, c), int(s * 0.26), (128, 128, 132), -1, cv2.LINE_AA)

    closed = str(position).upper() in ("CLOSED", "ON", "合", "1")
    ang = 0.0 if closed else 90.0        # 合位竖直，分位水平
    L = s * 0.36
    a = math.radians(ang - 90.0)
    x1, y1 = int(c - L * math.cos(a)), int(c - L * math.sin(a))
    x2, y2 = int(c + L * math.cos(a)), int(c + L * math.sin(a))
    cv2.line(img, (x1, y1), (x2, y2), (40, 40, 44), max(3, int(s * 0.10)), cv2.LINE_AA)
    cv2.circle(img, (x2, y2), max(3, int(s * 0.07)), (30, 30, 34), -1, cv2.LINE_AA)
    cv2.circle(img, (c, c), max(2, int(s * 0.05)), (60, 60, 64), -1, cv2.LINE_AA)
    # 位置指示牌
    if s >= 64:
        txt = "ON" if closed else "OFF"
        fs = s / 200.0
        tw = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)[0][0]
        cv2.putText(img, txt, (c - tw // 2, int(s * 0.94)),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, (30, 30, 34), max(1, s // 120), cv2.LINE_AA)
    return img


def render_anomaly_object(size_px: int, *, seed: int = 0) -> np.ndarray:
    """未知异常目标：形状不规则的异物。走 L3 通路，不进已知缺陷类别。"""
    rng = np.random.default_rng(seed)
    s = int(size_px)
    img = np.full((s, s, 3), 118, np.uint8)
    n = 7
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        r = s * (0.22 + 0.16 * rng.random())
        pts.append([int(s / 2 + r * math.cos(a)), int(s / 2 + r * math.sin(a))])
    cv2.fillPoly(img, [np.array(pts, np.int32)], (52, 88, 148), cv2.LINE_AA)
    cv2.polylines(img, [np.array(pts, np.int32)], True, (30, 52, 96),
                  max(1, s // 60), cv2.LINE_AA)
    return img
