"""像素密度：公式、渲染、判据三者必须一致。

这是全项目立论的锚点。方案书 §5.3 的推导链——0.5 % FS → 120 px → 3× 变焦
→ 6 m 距离上限——只有在"渲染图上量出来的像素宽度确实等于公式值"时才成立。
如果这个测试挂了，说明投影实现错了，后面所有精度指标都不用看。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from patrol.common.config import Config
from patrol.scene.optics import (PinholeCamera, distance_for_density,
                                 focal_px, hfov_at_zoom, pixel_density,
                                 stub_effective_pixel_ratio, zoom_for_density)
from patrol.scene.render import RenderOptions, SceneRenderer
from patrol.scene.world import World

W, D, THETA, P_MIN = 1920, 0.15, 60.0, 120.0


@pytest.fixture(scope="module")
def cfg():
    return Config.load()


@pytest.fixture(scope="module")
def world(cfg):
    return World(cfg)


def test_icd_calibration_cases():
    """ICD §3.2 的三行标定基准值。"""
    assert pixel_density(W, D, 1.0, 5.0, THETA) == pytest.approx(49.9, abs=0.1)
    assert pixel_density(W, D, 3.0, 5.0, THETA) == pytest.approx(149.6, abs=0.1)
    assert pixel_density(W, D, 3.0, 6.24, THETA) == pytest.approx(120.0, abs=0.2)


def test_two_hard_constraints():
    """由判据反推的两条硬约束，都要写进标定规范。"""
    z_req = P_MIN / pixel_density(W, D, 1.0, 5.0, THETA)
    assert z_req == pytest.approx(2.41, abs=0.01)
    assert z_req <= 3.0, "3× 光学变焦是下限，不满足则指针表无法自动读数"

    d_max = distance_for_density(W, D, 3.0, P_MIN, THETA)
    assert d_max == pytest.approx(6.24, abs=0.01)
    assert d_max >= 6.0, "巡检位到表计目标的距离上限取 6 m"


def test_stub_effective_pixel_ratio():
    """ICD §9.2：桩只有真机 2/3 的信息量，标定素材要布在 4 m 以内。"""
    assert stub_effective_pixel_ratio(1.0) == pytest.approx(1.0)
    assert stub_effective_pixel_ratio(2.0) == pytest.approx(1.0)
    assert stub_effective_pixel_ratio(3.0) == pytest.approx(2.0 / 3.0)
    d_stub = distance_for_density(W, D, 3.0, P_MIN / stub_effective_pixel_ratio(3.0), THETA)
    assert d_stub == pytest.approx(4.16, abs=0.01)


@pytest.mark.parametrize("zoom", [1.0, 1.5, 2.0, 2.5, 3.0])
def test_hfov_and_formula_are_equivalent(zoom):
    """变焦实现为视场角收缩，于是针孔投影自动满足像素密度公式。

    这条等价关系是渲染器正确性的基础：不是渲染完再去凑公式，而是公式本来
    就是投影的推论。
    """
    h = hfov_at_zoom(THETA, zoom)
    p_pinhole = focal_px(W, h) * D / 5.0
    p_formula = pixel_density(W, D, zoom, 5.0, THETA)
    assert p_pinhole == pytest.approx(p_formula, rel=1e-9)


@pytest.mark.parametrize("zoom,want", [(1.0, 49.9), (2.0, 99.8), (3.0, 149.6)])
def test_rendered_bbox_matches_formula(world, zoom, want):
    """**渲染图上量出来的表盘像素宽度 == 公式值。**

    误差来源只有贴图边缘的抗锯齿与四舍五入，容差取 3 %。
    """
    r = SceneRenderer(world, RenderOptions(hfov_at_1x_deg=THETA))
    wp = world.waypoints["WP-07"]
    tgt = world.by_id("TGT-01")
    cam = PinholeCamera(W, 1080, hfov_at_zoom(THETA, 1.0),
                        (wp.x_m, wp.y_m, world.camera_height_m), wp.yaw_deg, 0, 0)
    pan, tilt = cam.aim_offset_deg(tgt.position)
    _, meta = r.render(pose_xy_yaw=(wp.x_m, wp.y_m, wp.yaw_deg),
                       pan_deg=pan, tilt_deg=tilt, zoom=zoom)
    m = next(m for m in meta if m["target_id"] == "TGT-01")
    measured = m["bbox"][2] - m["bbox"][0]
    formula = pixel_density(W, m["target_size_m"], zoom, m["distance_m"], THETA)
    assert measured == pytest.approx(formula, rel=0.03)
    assert measured == pytest.approx(want, rel=0.03)


def test_wp07_is_the_calibration_case(world):
    """场景标定：WP-07 到 TGT-01 恰好 5 m，对应 ICD 的算例。"""
    wp = world.waypoints["WP-07"]
    tgt = world.by_id("TGT-01")
    d = world.distance_to(tgt, np.array([wp.x_m, wp.y_m, world.camera_height_m]))
    assert d == pytest.approx(5.0, abs=0.05)


def test_zoom_for_density_clips_to_range():
    """按需变焦（差异清单 C4）：算出来的倍率必须夹在 [1, 3]。"""
    assert zoom_for_density(1.0, 49.9, 120.0) == pytest.approx(2.406, abs=0.01)
    assert zoom_for_density(1.0, 10.0, 120.0) == 3.0      # 再远也不能超过光学上限
    assert zoom_for_density(3.0, 400.0, 120.0) == 1.0     # 已经够大就退回广角


def test_aim_offset_centers_the_target(world):
    """aim_offset 是增量语义：加到当前位姿上，目标应落在画面中心。

    与 ICD §3.5/§4.6 的示例一致（tilt: -2.0 + 1.6 = -0.4）。
    """
    wp = world.waypoints["WP-07"]
    tgt = world.by_id("TGT-01")
    origin = (wp.x_m, wp.y_m, world.camera_height_m)
    cam = PinholeCamera(W, 1080, hfov_at_zoom(THETA, 1.0), origin, wp.yaw_deg, 0, 0)
    pan, tilt = cam.aim_offset_deg(tgt.position)
    cam2 = PinholeCamera(W, 1080, hfov_at_zoom(THETA, 1.0), origin, wp.yaw_deg, pan, tilt)
    uv, _ = cam2.project(np.asarray([tgt.position]))
    assert uv[0][0] == pytest.approx(W / 2, abs=1.0)
    assert uv[0][1] == pytest.approx(1080 / 2, abs=1.0)
