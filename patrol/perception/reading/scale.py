"""标度变换与五点标定。方案书 §6.1.1 / §6.1.2。

指针表的刻度盘在设计上是均匀的，所以转角到工程量是线性变换：

    R = R_min + (θ − θ_min) / (θ_max − θ_min) × (R_max − R_min)

以一块 0~1.6 MPa 的压力表为例，θ_min = 0°，θ_max = 270°，测得 θ = 135°
时 R = 0.8 MPa。

标定的目的是确定 θ_min 与 θ_max 这两个参数，并验证线性度是否满足指标。
采用五点静态标定法：0 %、25 %、50 %、75 %、100 % 五个位置，每点重复 10 次
取平均，最小二乘拟合，残差与量程之比即线性度。

线性度须 ≤0.4 % FS，这是把标度变换按线性处理的前提。重复性由同一位置
10 次测量的极差衡量，须 ≤0.3 % FS。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


def wrap180(deg: float) -> float:
    """把角度折算到 (-180, 180]。"""
    return ((float(deg) + 180.0) % 360.0) - 180.0


def angle_to_value(angle_deg: float, *, range_min: float, range_max: float,
                   sweep_deg: float, zero_offset_deg: float) -> float:
    """标度变换（正变换）：指针转角 → 工程量。

    与 scene/gauges.value_to_angle 互为逆运算，tests 里往返验证。
    这里**不夹取**到量程内：指针指到量程外是一种需要被看见的异常，
    夹取会把它藏起来。
    """
    span = float(sweep_deg)
    if abs(span) < 1e-9:
        return float(range_min)
    frac = (float(angle_deg) - float(zero_offset_deg)) / span
    return float(range_min) + frac * (float(range_max) - float(range_min))


def value_to_angle(value: float, *, range_min: float, range_max: float,
                   sweep_deg: float, zero_offset_deg: float) -> float:
    """逆变换。标定时由标称读数反推期望转角。"""
    span = float(range_max) - float(range_min)
    frac = 0.0 if abs(span) < 1e-12 else (float(value) - float(range_min)) / span
    return float(zero_offset_deg) + frac * float(sweep_deg)


def in_normal_band(value, band) -> bool | None:
    """读数是否落在正常区间。正常区间由标定阶段配置。"""
    if value is None or not band or len(band) != 2:
        return None
    try:
        return float(band[0]) <= float(value) <= float(band[1])
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 五点标定
@dataclass
class CalibrationPoint:
    """一个标定点：标称读数 + 该点重复测量得到的一组转角。"""

    nominal_value: float
    angles_deg: list[float] = field(default_factory=list)

    @property
    def mean_angle(self) -> float:
        return float(np.mean(self.angles_deg)) if self.angles_deg else float("nan")

    @property
    def range_angle(self) -> float:
        """极差，用于算重复性。"""
        a = self.angles_deg
        return float(max(a) - min(a)) if len(a) >= 2 else 0.0


@dataclass
class CalibrationResult:
    """标定曲线：以转角为横轴、标称读数为纵轴的最小二乘直线。"""

    slope: float                 # 工程量每度
    intercept: float
    linearity_pct_fs: float      # 最大残差 / 量程
    repeatability_pct_fs: float  # 各点极差折算到读数的最大值
    residuals_pct_fs: list[float]
    span: float
    n_points: int

    def apply(self, angle_deg: float) -> float:
        """用标定出来的系数做标度变换。"""
        return self.slope * float(angle_deg) + self.intercept

    def passes(self, *, linearity_limit: float = 0.4,
               repeatability_limit: float = 0.3) -> bool:
        return (self.linearity_pct_fs <= linearity_limit
                and self.repeatability_pct_fs <= repeatability_limit)

    def report(self) -> str:
        lines = [
            "标定曲线  R = %.6f·θ + %.6f" % (self.slope, self.intercept),
            "线性度    %.3f %% FS   (限值 0.4)  %s" % (
                self.linearity_pct_fs, "合格" if self.linearity_pct_fs <= 0.4 else "超差"),
            "重复性    %.3f %% FS   (限值 0.3)  %s" % (
                self.repeatability_pct_fs,
                "合格" if self.repeatability_pct_fs <= 0.3 else "超差"),
            "各点残差  " + "  ".join("%+.3f" % r for r in self.residuals_pct_fs),
        ]
        return "\n".join(lines)


def calibrate(points: list[CalibrationPoint], *, range_min: float,
              range_max: float) -> CalibrationResult:
    """五点静态标定。

    步骤（方案书 §6.1.2）：
      1. 指针依次停在量程 0/25/50/75/100 % 五个位置
      2. 每个位置采集图像解算转角，重复 10 次取平均
      3. 以转角为横轴、标称读数为纵轴作图，即标定曲线
      4. 五点做最小二乘直线拟合，斜率与截距即标度变换系数
      5. 各点相对拟合直线的残差，最大残差与量程之比即线性度
    """
    usable = [p for p in points if p.angles_deg]
    if len(usable) < 2:
        raise ValueError("至少需要两个有效标定点，当前 %d" % len(usable))
    span = float(range_max) - float(range_min)
    if abs(span) < 1e-12:
        raise ValueError("量程为零")

    x = np.array([p.mean_angle for p in usable], dtype=float)
    y = np.array([p.nominal_value for p in usable], dtype=float)
    slope, intercept = np.polyfit(x, y, 1)

    fitted = slope * x + intercept
    resid_pct = (fitted - y) / span * 100.0
    linearity = float(np.max(np.abs(resid_pct)))

    # 重复性：转角极差按斜率折算到读数，再折算到量程百分比
    rep = max((abs(p.range_angle * slope) / abs(span) * 100.0) for p in usable)

    return CalibrationResult(
        slope=float(slope), intercept=float(intercept),
        linearity_pct_fs=linearity, repeatability_pct_fs=float(rep),
        residuals_pct_fs=[float(v) for v in resid_pct],
        span=float(span), n_points=len(usable),
    )


def error_budget(pixel_density_px: float, *, sweep_deg: float = 270.0,
                 tip_px: float = 1.0, center_px: float = 1.0,
                 perspective_deg: float = 0.3, quantization_bits: int = 12,
                 linearity_pct_fs: float = 0.40) -> dict:
    """误差清单与方和根合成。方案书表 6-1。

    各项误差相互独立，按方和根合成。表盘直径 120 px 时合成 0.65 % FS，
    略超 0.5 % 指标——这说明在临界条件下精度是紧张的，实际部署要留余量：
    触发阈值定在 120 px，而复核后的实际像素密度通常可达 150 px 以上，
    此时前两项降到 0.28 % 附近，合成后约 0.56 % FS。

    这个函数把那张表变成可计算的，便于扫描像素密度看误差怎么退化。
    """
    r = max(1e-6, float(pixel_density_px) / 2.0)
    d_tip = math.degrees(math.atan(float(tip_px) / r))
    d_center = math.degrees(math.atan(float(center_px) / r))
    to_fs = lambda deg: abs(deg) / float(sweep_deg) * 100.0    # noqa: E731
    terms = {
        "指针尖端定位": to_fs(d_tip),
        "表盘中心定位": to_fs(d_center),
        "透视校正残差": to_fs(perspective_deg),
        "A/D 量化": 100.0 / (2 ** int(quantization_bits)),
        "标度变换线性度": float(linearity_pct_fs),
    }
    total = math.sqrt(sum(v * v for v in terms.values()))
    return {"terms": terms, "total_pct_fs": total,
            "pixel_density_px": float(pixel_density_px),
            "meets_0p5": total <= 0.5}
