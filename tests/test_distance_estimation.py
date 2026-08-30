"""距离反算：zoom 必须代入，焦距只有一个。

盯的是一处**切到真权重那天才会暴露、而且不报错**的失效。

立论链是"巡航 1× 便宜地扫，可疑就停车对准变焦再看一眼"。复核态 3× 时 bbox
高度涨 3 倍。距离由 bbox 高度反算，若反算时不代入当前变焦倍率，距离会算成
真值的 1/3，像素密度虚高——于是 fusion.py 那条"密度不达标就不下读数类结论"
的门槛**静默失效**：系统认为观测条件达标，照常出读数结论。

合成检测器直接透传场景真值距离（oracle），正好把这个缺陷掩盖住；切到 yolo
的那一刻掩盖消失，而缺陷不抛异常、不打日志。所以必须由测试钉住，而且这些
测试**不需要任何权重、不需要 ultralytics** 就能跑——这正是它们的价值所在：
在"整个项目最关键的一次合流"之前就能红。

判据用**恒等式**而不是容差：把反算距离代回 pixel_density()，结果必须恰好
等于输入的 bbox 高度，与 zoom 无关。zoom 漏传、或焦距算错，恒等式立刻破。
"""
from __future__ import annotations

import math

import pytest

from patrol.scene.optics import (distance_from_bbox_height, focal_px,
                                 pixel_density, vfov_from_hfov)

# 与 configs/camera.yaml 一致。这里写死是**故意的**：这些是方案书 §5.3 推导
# 所依据的标称值，测试的职责就是钉住"配置改了推导要跟着重算"。
W, H, THETA, D, P_MIN = 1920, 1080, 60.0, 0.15, 120.0

ZOOMS = (1.0, 1.5, 2.0, 2.4, 3.0)
# 下界取 30 px 而不是 20：30 px 的框在 3× 下反算出 24.9 m，还在 30 m 限幅内；
# 20 px 会算到 37 m 被夹住，恒等式对夹住的点本来就不成立。
HEIGHTS = (30.0, 49.9, 120.0, 149.6, 300.0, 600.0)


@pytest.mark.parametrize("zoom", ZOOMS)
@pytest.mark.parametrize("bbox_h", HEIGHTS)
def test_density_roundtrip_is_exact_at_any_zoom(bbox_h, zoom):
    """p(d(h)) ≡ h，与 zoom 无关。这是整个文件的核心判据。

    代数上：d = f₀·D·z/h 且 p = W·D·z/(2·d·tan(θ₀/2))，代入后 z 与 D 全部
    约掉，只剩 p = h。所以这条恒等式对任何 zoom、任何目标尺寸都必须成立，
    不是"近似相等"而是浮点意义上的相等。
    """
    d = distance_from_bbox_height(bbox_h, D, zoom, W, THETA)
    assert 0.3 < d < 30.0, "先确认没被夹到限幅上，否则恒等式不适用"
    assert pixel_density(W, D, zoom, d, THETA) == pytest.approx(bbox_h, rel=1e-9)


@pytest.mark.parametrize("zoom", ZOOMS)
def test_zoom_must_be_threaded_through(zoom):
    """同一目标、同一真实距离，在任意变焦下反算出的距离必须一致。

    **这就是那条回归测试。**复核态 3× 时 bbox 高 3 倍；如果 zoom 没传进来，
    反算结果会是真值的 1/z。断言距离而不是断言 bbox 高度，是因为距离才是
    进证据包、喂给密度门槛的那个量。
    """
    true_d = 5.0
    bbox_h = pixel_density(W, D, zoom, true_d, THETA)      # 该变焦下的真实框高
    got = distance_from_bbox_height(bbox_h, D, zoom, W, THETA)
    assert got == pytest.approx(true_d, rel=1e-9), (
        "zoom=%.1f 下反算距离 %.3f m，真值 %.3f m。差 %.2f 倍——"
        "这个比值等于 zoom 就说明 zoom 没有代入反算。"
        % (zoom, got, true_d, true_d / got))


def test_zoom_default_would_break_it():
    """反过来钉一次：按 1× 反算 3× 的框，必然错成 1/3。

    这条不是测实现，是把**失效的样子**写下来，免得后来人把 zoom 参数改回
    默认值时以为"反正测试是绿的"。
    """
    true_d = 5.0
    bbox_h_at_3x = pixel_density(W, D, 3.0, true_d, THETA)
    wrong = distance_from_bbox_height(bbox_h_at_3x, D, 1.0, W, THETA)   # 假装 zoom=1
    assert wrong == pytest.approx(true_d / 3.0, rel=1e-9)


def test_there_is_only_one_focal_length():
    """方形像素下竖直焦距恒等于水平焦距。

    H/(2·tan(vfov/2)) 代入 tan(vfov/2)=tan(θ/2)·H/W 后 H 约掉，还原成
    focal_px(W, θ)。所以任何"先算 vfov 再由 H 反推焦距"的写法都是绕路，
    而绕路正是当初把线性缩放塞进来的地方。
    """
    f_h = focal_px(W, THETA)
    f_v = H / (2.0 * math.tan(math.radians(vfov_from_hfov(THETA, W, H)) / 2.0))
    assert f_v == pytest.approx(f_h, rel=1e-12)


def test_vfov_is_not_linear_in_aspect_ratio():
    """视场角不能按画幅比例线性缩放。

    640×640 下线性与真值恰好相等，所以这个错误在方形输入上**测不出来**；
    实际配置是 1920×1080，必须在这个画幅上钉住。
    """
    linear = THETA * H / W                       # 曾经的写法
    correct = vfov_from_hfov(THETA, W, H)
    assert correct == pytest.approx(35.9834, abs=1e-3)
    assert linear == pytest.approx(33.75, abs=1e-3)
    assert abs(linear / correct - 1.0) > 0.05, "1920×1080 下两者相差 6.2 %"

    # 方形画幅是这个 bug 的盲区，一并记录
    assert vfov_from_hfov(THETA, 640, 640) == pytest.approx(THETA * 640 / 640, rel=1e-9)


@pytest.mark.parametrize("true_d", (6.5, 8.0, 12.0, 17.0))
def test_density_gate_is_not_silently_passed(true_d):
    """密度门槛不能被虚高的密度骗过去——这条是后果测试，不是算法测试。

    3× 变焦下真实 d_max = 6.24 m（p 跌到 120 px 判据线）。漏传 zoom 时距离
    被低估 3 倍、密度虚高约 2.8 倍，于是 6.24 m 到 17.47 m 之间的目标：
    真实观测条件**不达标**（该判 INCONCLUSIVE 交人复核），但系统读到的密度
    在判据线以上，照常出读数结论。这个区间就是静默失效窗口。
    """
    bbox_h = pixel_density(W, D, 3.0, true_d, THETA)
    assert pixel_density(W, D, 3.0, true_d, THETA) < P_MIN, "构造前提：真实密度已不达标"

    d = distance_from_bbox_height(bbox_h, D, 3.0, W, THETA)
    reported = pixel_density(W, D, 3.0, d, THETA)
    assert reported < P_MIN, (
        "真实距离 %.1f m 处密度只有 %.1f px，系统却报 %.1f px（判据 %.0f px）——"
        "读数类结论会被放行，而它本该交人。" % (true_d, bbox_h, reported, P_MIN))


def test_degenerate_bbox_returns_fallback():
    """退化框不参与反算。

    bbox 高 ≤1 px 时反算会炸成任意大的距离，这里退回兜底值。注意这是一个
    **静默兜底**，与网关"越界拒绝而不截断"的原则不同调——见 optics.py 里
    fallback_m 的说明。
    """
    assert distance_from_bbox_height(0.0, D, 3.0, W, THETA) == 8.0
    assert distance_from_bbox_height(1.0, D, 3.0, W, THETA) == 8.0
    assert distance_from_bbox_height(1.5, D, 3.0, W, THETA) != 8.0


def test_clamped_to_physical_range():
    """反算结果夹在 [0.3, 30] m。配电室巡检不存在 30 m 外的表计。"""
    assert distance_from_bbox_height(100000.0, D, 1.0, W, THETA) == pytest.approx(0.3)
    assert distance_from_bbox_height(1.01, D, 1.0, W, THETA) == pytest.approx(30.0)


def test_agrees_with_icd_calibration_cases():
    """与 ICD §3.2 的标定基准值对齐，闭合到同一套数上。

    p(z=1,d=5)=49.9、p(z=3,d=5)=149.6 是 tests/test_pixel_density.py 已经钉住
    的两个数；从这两个框高反算回去必须都得到 5 m。
    """
    assert distance_from_bbox_height(49.9, D, 1.0, W, THETA) == pytest.approx(5.0, abs=0.01)
    assert distance_from_bbox_height(149.6, D, 3.0, W, THETA) == pytest.approx(5.0, abs=0.01)
