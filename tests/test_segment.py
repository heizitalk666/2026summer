"""L2 分割：读数的第二种实现路线。

**这一层最大的风险不是不准，是"看起来在用、其实没在用"**，或者反过来——
默默启用了一个没学过东西的模型，把读数带偏而日志里一句话都没有。所以用例
的重心在接口纪律，而不是精度指标（精度归 tools/bench_models.py，它按像素
密度分档比，那才是有意义的口径）。

三条纪律：

1. 默认不加载任何权重——默认配置跑出来必须是当前最好的结果
2. 权重缺席时**大声报错**，绝不悄悄退回随机权重
3. 分割给不出可用掩膜时，读数自动退回几何法，不是读数失败
"""
from __future__ import annotations

import numpy as np
import pytest

from patrol.common.config import Config
from patrol.perception.reading.pointer import read_pointer_gauge
from patrol.perception.segment.base import GaugeMask, build_segmenter
from patrol.perception.segment.pixel import (N_CLASS, N_FEAT, WORK, features,
                                             prepare, softmax)
from patrol.scene.gauges import render_pointer_gauge

PRIORS = {"kind": "POINTER_GAUGE", "unit": "MPa", "range_min": 0.0,
          "range_max": 1.6, "sweep_deg": 270.0, "zero_offset_deg": -135.0,
          "normal_band": [0.4, 1.2], "major_ticks": 27}


def dial(px=220, value=0.85):
    import cv2
    src = render_pointer_gauge(512, value=value, range_min=0.0, range_max=1.6,
                               sweep_deg=270.0, zero_offset_deg=-135.0,
                               major_ticks=27, unit="MPa", normal_band=(0.4, 1.2))
    small = cv2.resize(src, (px, px), interpolation=cv2.INTER_AREA)
    pad = 40
    img = np.full((px + 2 * pad, px + 2 * pad, 3), 150, np.uint8)
    img[pad:pad + px, pad:pad + px] = small
    return img, (pad, pad, pad + px, pad + px)


def fake_weights(tmp_path, *, W=None):
    """一份形状合法的权重。内容随机——测的是接口，不是精度。"""
    rng = np.random.default_rng(0)
    p = tmp_path / "pixel.npz"
    np.savez(p, W=(rng.normal(0, 0.5, (N_FEAT, N_CLASS)) if W is None else W),
             mu=np.zeros(N_FEAT, np.float32), sigma=np.ones(N_FEAT, np.float32),
             val_iou=np.float32(0.31))
    return p


# ---------------------------------------------------------------- 工厂
def test_default_loads_nothing():
    """**默认配置跑出来必须是当前最好的结果。**

    bench_models 实测：在本项目的表盘上级联并不比几何法准，而且慢 2.4 倍。
    所以默认 builtin，读数走几何法。这条钉的是这个决定不被无声改掉。
    """
    assert build_segmenter(Config.load()) is None


@pytest.mark.parametrize("kind", ["builtin", "off", "none", "geometric"])
def test_all_the_no_op_spellings_work(kind):
    cfg = Config.load(overrides={"perception": {"segmenter": {"backend": kind}}})
    assert build_segmenter(cfg) is None


def test_unknown_backend_is_loud():
    cfg = Config.load(overrides={"perception": {"segmenter": {"backend": "unet"}}})
    with pytest.raises(ValueError):
        build_segmenter(cfg)


def test_missing_weights_raise_instead_of_falling_back_silently(tmp_path):
    """**悄悄用随机权重是最坏的选择。**

    它会输出一张看似有内容的掩膜，把读数带偏，而日志里一句话都没有。
    """
    cfg = Config.load(overrides={"perception": {"segmenter": {
        "backend": "npz", "weights": str(tmp_path / "不存在.npz")}}})
    with pytest.raises(FileNotFoundError):
        build_segmenter(cfg)


def test_weight_shape_mismatch_is_caught(tmp_path):
    """特征定义改了却没重训，形状对不上——必须当场发现，不能凑合跑。"""
    p = tmp_path / "old.npz"
    np.savez(p, W=np.zeros((N_FEAT - 2, N_CLASS), np.float32),
             mu=np.zeros(N_FEAT - 2, np.float32),
             sigma=np.ones(N_FEAT - 2, np.float32))
    from patrol.perception.segment.pixel import NpzSegmenter
    with pytest.raises(ValueError):
        NpzSegmenter(None, weights=p)


# ---------------------------------------------------------------- 特征
def test_features_are_scale_normalised():
    """**特征里有固定长度的线状核，尺度必须先归一化。**

    不归一的话同一块表在 60 px 和 200 px 下算出来的"长划痕响应"完全不是
    一回事，训出来的模型换个距离就失效。
    """
    a = features(dial(80)[0])
    b = features(dial(300)[0])
    assert a.shape == b.shape == (WORK, WORK, N_FEAT)


def test_prepare_matches_the_feature_grid():
    assert prepare(dial(90)[0]).shape[:2] == (WORK, WORK)


def test_stroke_length_separates_needle_from_ticks():
    """**这一维是针与刻度的唯一分界。**

    两者都是从圆心往外的深色细条，亮度、宽度、梯度方向全一样，逐像素的
    局部外观根本分不开——第一版没有这一维，针的验证 IoU 只有 0.005。
    针从圆心伸到 0.80R，刻度只有 0.12R，连通域的外接对角线把这个差别量出来。
    """
    import cv2
    from patrol.perception.segment.pixel import _stroke_stats
    from patrol.scene.gauges import SEG_LABELS, render_pointer_gauge_mask

    q = prepare(dial(240)[0])
    g = cv2.cvtColor(q, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    length, thin = _stroke_stats(1.0 - g)

    # 用真掩膜取标签：dial() 把表画在 220 px 见方、四周留 40 px 边的画布上
    m = render_pointer_gauge_mask(512, value=0.85, range_min=0.0, range_max=1.6,
                                  sweep_deg=270.0, zero_offset_deg=-135.0,
                                  major_ticks=27)
    px, pad = 220, 40
    canvas = np.zeros((px + 2 * pad, px + 2 * pad), np.uint8)
    canvas[pad:pad + px, pad:pad + px] = cv2.resize(
        m, (px, px), interpolation=cv2.INTER_NEAREST)
    lab = cv2.resize(canvas, (WORK, WORK), interpolation=cv2.INTER_NEAREST)
    needle, ticks = lab == SEG_LABELS["needle"], lab == SEG_LABELS["ticks"]
    assert needle.sum() > 10 and ticks.sum() > 10

    assert float(length[needle].mean()) > float(length[ticks].mean()) * 1.3, (
        "笔画长度 针 %.3f vs 刻度 %.3f，区分不开"
        % (length[needle].mean(), length[ticks].mean()))


def test_stroke_stats_are_orientation_free():
    """**朝向无关是这一维的硬要求。**

    上一版用固定朝向的线状结构元做开运算，31 px 长、2 px 宽的针只容得下
    约 3.7° 的朝向误差；六个朝向覆盖不住，指针指到两档之间时这一维直接反
    过来（针的响应比刻度还弱）。连通域没有这个问题——这条用例把指针转一圈，
    要求每个角度都测得出。
    """
    import cv2
    from patrol.perception.segment.pixel import _stroke_stats
    got = []
    for v in (0.05, 0.3, 0.55, 0.8, 1.05, 1.3, 1.55):
        q = prepare(dial(240, value=v)[0])
        g = cv2.cvtColor(q, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        length, _thin = _stroke_stats(1.0 - g)
        got.append(float(length.max()))
    assert min(got) > 0.3, "某些指针角度下完全测不到长笔画：%s" % [
        round(x, 2) for x in got]


def test_softmax_is_a_distribution():
    z = np.array([[1.0, 2.0, 3.0, 4.0]], np.float32)
    p = softmax(z)
    assert abs(float(p.sum()) - 1.0) < 1e-5 and float(p.max()) < 1.0


# ---------------------------------------------------------------- 级联
def test_segmenter_output_lands_in_roi_coordinates(tmp_path):
    """掩膜的坐标系是**传进来的那张 patch**，不是原图。

    弄错的话它会和图像走不同的仿射校正，针的概率落在错误的角度上——
    而读数照样会给出一个看起来正常的值。
    """
    from patrol.perception.segment.pixel import NpzSegmenter
    seg = NpzSegmenter(None, weights=fake_weights(tmp_path))
    img, _box = dial(220)
    patch = img[40:260, 40:260]
    gm = seg.segment(patch)
    assert isinstance(gm, GaugeMask)
    assert gm.needle.shape == patch.shape[:2]
    assert 0.0 <= float(gm.needle.min()) and float(gm.needle.max()) <= 1.0


def test_tiny_roi_is_skipped(tmp_path):
    from patrol.perception.segment.pixel import NpzSegmenter
    seg = NpzSegmenter(None, weights=fake_weights(tmp_path))
    assert seg.segment(np.zeros((20, 20, 3), np.uint8)) is None
    assert seg.segment(np.zeros((0, 0, 3), np.uint8)) is None


def test_reading_falls_back_to_geometry_when_segmentation_is_useless(tmp_path):
    """**分割给不出可用掩膜时，读数退回几何法，而不是读数失败。**

    学习模型缺席应当让读数退回到今天这个水平，不该把整条复核带走。
    这里给一份必然输出全零"针"概率的权重来逼出这条路径。
    """
    W = np.zeros((N_FEAT, N_CLASS), np.float32)
    W[:, 1] = 20.0                      # 强行全判成"盘面"，针的概率恒为 ~0
    from patrol.perception.segment.pixel import NpzSegmenter
    seg = NpzSegmenter(None, weights=fake_weights(tmp_path, W=W))
    img, box = dial(220, value=0.85)
    r = read_pointer_gauge(img, box, PRIORS, segmenter=seg)
    plain = read_pointer_gauge(img, box, PRIORS)
    assert r.ok and plain.ok
    assert abs(r.value - plain.value) < 0.02, (
        "分割没给出可用掩膜时读数应当与纯几何一致：%.4f vs %.4f"
        % (r.value, plain.value))


def test_a_throwing_segmenter_does_not_break_the_reading():
    class Boom:
        def segment(self, patch):
            raise RuntimeError("推理炸了")

        def model_info(self):
            return {}

    img, box = dial(220, value=0.6)
    r = read_pointer_gauge(img, box, PRIORS, segmenter=Boom())
    assert r.ok, "分割抛异常把读数带走了"


def test_cascade_still_reads_the_right_value(tmp_path):
    """接上分割之后读数仍要对。级联替换的只是"哪些像素是针"这一步。"""
    from patrol.perception.segment.pixel import NpzSegmenter
    seg = NpzSegmenter(None, weights=fake_weights(tmp_path))
    for v in (0.3, 0.85, 1.4):
        img, box = dial(240, value=v)
        r = read_pointer_gauge(img, box, PRIORS, segmenter=seg)
        assert r.ok, "读不出来 value=%.2f" % v
        assert abs(r.value - v) < 0.16, "读成了 %.3f，真值 %.2f" % (r.value, v)


def test_gauge_mask_ok_flag():
    assert not GaugeMask(needle=np.zeros((8, 8), np.float32)).ok
    m = np.zeros((8, 8), np.float32)
    m[4, 4] = 0.9
    assert GaugeMask(needle=m).ok
