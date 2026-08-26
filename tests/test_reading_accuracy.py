"""读数精度：方案书 §2.2 的三项指标必须可测、可复现。

**这是全项目最要紧的一组测试。**它验证的不是"代码不崩"，而是"测得准"——
测控系统课题的核心评价。

纪律：读数算法只拿到图像与检测框，场景真值只用于最后打分。如果哪天这些
测试的误差曲线变平（不随像素密度退化），那不是算法变好了，是算法开始
偷看真值了，必须查。
"""
from __future__ import annotations

import numpy as np
import pytest

from patrol.common.config import Config
from patrol.perception.reading.pointer import read_pointer_gauge
from patrol.perception.reading.scale import (CalibrationPoint, angle_to_value,
                                             calibrate, error_budget,
                                             in_normal_band, value_to_angle)
from patrol.scene.gauges import render_pointer_gauge
from patrol.scene.gauges import value_to_angle as gauge_value_to_angle
from patrol.scene.world import World

PRIORS = dict(range_min=0.0, range_max=1.6, sweep_deg=270.0,
              zero_offset_deg=-135.0, unit="MPa", normal_band=(0.30, 1.20))
SPAN = 1.6


def _read(size: int, value: float):
    tex = render_pointer_gauge(size, value=value, range_min=0.0, range_max=1.6,
                               unit="MPa", normal_band=(0.30, 1.20))
    return read_pointer_gauge(tex, (0, 0, size - 1, size - 1), PRIORS)


# ---------------------------------------------------------------- 标度变换
@pytest.mark.parametrize("v", [0.0, 0.42, 0.8, 1.2, 1.6])
def test_scale_transform_roundtrip(v):
    """正变换与逆变换必须互为逆运算——渲染用一个、读数用另一个，错一个就全错。"""
    a = gauge_value_to_angle(v, 0.0, 1.6, 270.0, -135.0)
    back = angle_to_value(a, range_min=0.0, range_max=1.6, sweep_deg=270.0,
                          zero_offset_deg=-135.0)
    assert back == pytest.approx(v, abs=1e-9)
    assert value_to_angle(v, range_min=0.0, range_max=1.6, sweep_deg=270.0,
                          zero_offset_deg=-135.0) == pytest.approx(a, abs=1e-9)


def test_scale_transform_matches_design_example():
    """方案书 §6.1.1 的算例：0~1.6 MPa 的表，θ=135° 时读数 0.8 MPa。"""
    v = angle_to_value(0.0, range_min=0.0, range_max=1.6, sweep_deg=270.0,
                       zero_offset_deg=-135.0)
    assert v == pytest.approx(0.8, abs=1e-9)


def test_normal_band():
    assert in_normal_band(0.42, (0.30, 1.20)) is True
    assert in_normal_band(0.10, (0.30, 1.20)) is False
    assert in_normal_band(None, (0.30, 1.20)) is None


# ---------------------------------------------------------------- 误差预算
def test_error_budget_matches_design_table():
    """方案书表 6-1：120 px 时合成 0.65 % FS，略超 0.5 % 指标。

    这不是算法的失败，是设计文档自己承认的：在 120 像素的临界条件下精度是
    紧张的，实际部署要靠复核后更高的像素密度获得余量。
    """
    e120 = error_budget(120.0)
    assert e120["total_pct_fs"] == pytest.approx(0.65, abs=0.01)
    assert not e120["meets_0p5"], "文档明说 120 px 时略超指标"
    e150 = error_budget(150.0)
    assert e150["total_pct_fs"] == pytest.approx(0.577, abs=0.01)
    # 误差必须随像素密度单调下降
    tot = [error_budget(p)["total_pct_fs"] for p in (50, 80, 120, 150, 200, 300)]
    assert all(tot[i] > tot[i + 1] for i in range(len(tot) - 1))


# ---------------------------------------------------------------- 读数
@pytest.mark.parametrize("value", [0.0, 0.2, 0.42, 0.8, 1.0, 1.2, 1.6])
def test_reads_full_range(value):
    r = _read(300, value)
    assert r.ok, r.fail_reason
    assert abs(r.value - value) / SPAN * 100 <= 0.5


def test_error_degrades_with_pixel_density():
    """**误差必须真的随像素密度退化。**

    这是全项目立论的落点：巡航态 50 px 读不准、复核态 150 px 读得准，所以
    才需要主动变焦补拍。如果这条曲线是平的，说明读数算法在作弊。
    """
    vals = np.linspace(0.05, 1.55, 9)
    curve = []
    for size in (60, 120, 300):
        errs = [abs(_read(size, float(v)).value - v) / SPAN * 100
                for v in vals if _read(size, float(v)).ok]
        curve.append(float(np.mean(errs)))
    assert curve[0] > curve[1] > curve[2], "误差没有随像素密度下降：%s" % curve
    assert curve[0] > 2 * curve[2], "50 px 与 300 px 的差距太小，可疑"


def test_reading_is_not_reading_ground_truth():
    """防作弊：同一张图配不同的量程先验，读数必须按先验线性缩放。

    如果算法偷看了场景真值，换量程后读数不会变；真算法解算的是角度，
    换量程只是换了标度变换的系数。
    """
    tex = render_pointer_gauge(300, value=0.8, range_min=0.0, range_max=1.6,
                               unit="MPa", normal_band=None)
    a = read_pointer_gauge(tex, (0, 0, 299, 299), dict(PRIORS))
    b = read_pointer_gauge(tex, (0, 0, 299, 299),
                           dict(PRIORS, range_min=0.0, range_max=16.0))
    assert a.ok and b.ok
    assert a.angle_deg == pytest.approx(b.angle_deg, abs=1e-6), "角度与量程无关"
    assert b.value == pytest.approx(a.value * 10.0, rel=0.02), "读数应按量程缩放"


def test_skewed_view_lowers_confidence_and_is_flagged():
    """视角过斜时 b/a < 0.85，方案书 §6.1.4 要求由状态机下发云台调整重新拍摄。"""
    import cv2
    tex = render_pointer_gauge(300, value=0.8, range_min=0.0, range_max=1.6)
    # 水平压缩到 0.6，模拟大角度斜视
    squashed = cv2.resize(tex, (180, 300))
    r = read_pointer_gauge(squashed, (0, 0, 179, 299), PRIORS)
    assert r.ok
    assert r.axis_ratio < 0.85, "应当识别出视角过斜"


# ---------------------------------------------------------------- 五点标定
def _collect_points(size: int, repeats: int, noise_sigma: float, seed: int):
    """五点法，每点重复 repeats 次。噪声模拟每次独立抓拍的差异。"""
    rng = np.random.default_rng(seed)
    pts = []
    for frac in (0.0, 0.25, 0.50, 0.75, 1.00):
        nominal = frac * 1.6
        cp = CalibrationPoint(nominal_value=nominal)
        for _ in range(repeats):
            tex = render_pointer_gauge(size, value=nominal, range_min=0.0,
                                       range_max=1.6, unit="MPa",
                                       normal_band=(0.30, 1.20))
            if noise_sigma > 0:
                tex = np.clip(tex.astype(np.float32)
                              + rng.normal(0, noise_sigma, tex.shape), 0, 255
                              ).astype(np.uint8)
            r = read_pointer_gauge(tex, (0, 0, size - 1, size - 1), PRIORS)
            if r.ok:
                cp.angles_deg.append(r.angle_deg)
        pts.append(cp)
    return pts


def test_five_point_calibration_meets_spec():
    """方案书 §6.1.2 的五点静态标定，§2.2 的三项精度指标。

    像素密度取 200 px：这是复核态实际可达的水平（3× 变焦、4 m 距离约 187 px），
    也是方案书 §11.2 写明"像素密度 150 px 以上"的口径。
    """
    pts = _collect_points(size=200, repeats=10, noise_sigma=2.0, seed=7)
    res = calibrate(pts, range_min=0.0, range_max=1.6)

    assert res.n_points == 5
    # 标定曲线的斜率应接近 量程/扫过角 = 1.6/270
    assert res.slope == pytest.approx(1.6 / 270.0, rel=0.03)
    assert res.linearity_pct_fs <= 0.4, res.report()
    assert res.repeatability_pct_fs <= 0.3, res.report()

    # 基本误差：标定后各次读数与标称值的最大偏差
    basic = max(abs(res.apply(a) - p.nominal_value) / 1.6 * 100
                for p in pts for a in p.angles_deg)
    assert basic <= 0.5, "基本误差 %.3f %% FS 超差\n%s" % (basic, res.report())


def test_calibration_curve_is_linear_by_design():
    """指针表刻度盘均匀，所以标定曲线理论上是直线。残差不应有系统性弯曲。"""
    pts = _collect_points(size=300, repeats=4, noise_sigma=0.0, seed=3)
    res = calibrate(pts, range_min=0.0, range_max=1.6)
    r = np.array(res.residuals_pct_fs)
    # 残差符号不应全部同号（那意味着用直线拟合了一条曲线）
    assert not (np.all(r > 0.05) or np.all(r < -0.05)), \
        "残差同号，说明刻度盘不是均匀的，应改用分段拟合：%s" % r.tolist()


def test_calibration_rejects_too_few_points():
    with pytest.raises(ValueError):
        calibrate([CalibrationPoint(0.0, [1.0])], range_min=0.0, range_max=1.6)


# ---------------------------------------------------------------- 场景一致性
def test_scene_priors_do_not_leak_truth():
    """priors 是标定阶段可知的先验，**不含当前读数**。"""
    w = World(Config.load())
    t = w.by_id("TGT-01")
    pri = t.priors
    assert "value" not in pri, "先验里不能有真值，否则读数算法可以偷看"
    assert pri["range_min"] == 0.0 and pri["range_max"] == 1.6
    assert t.true_value is not None
