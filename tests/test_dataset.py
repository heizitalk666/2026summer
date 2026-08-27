"""合成数据集：掩膜必须与图像逐像素对齐。

**这是整个合成数据方案的地基，也是最容易悄悄塌掉的一块。**

掩膜是"把同一套几何参数画到标签画布上"得来的，不是从图像里分割出来的。
好处是免费且天生对齐；风险是 `render_pointer_gauge` 和
`render_pointer_gauge_mask` 一旦有一处比例常数不同步，掩膜就整体偏一点，
而**这件事在任何统计量上都看不出来**：像素数、类别比例、分布图全都正常，
只有训出来的分割模型系统性偏几度，最后表现为读数有个查不出来的常值偏差。

所以这里不测"能不能生成"，测的是对齐本身：针的掩膜必须落在图上真正黑的
那些像素处。比例常数一改，这条立刻红。
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from patrol.common.config import Config
from patrol.scene.gauges import (SEG_LABELS, SEG_NAMES, render_pointer_gauge,
                                 render_pointer_gauge_mask, value_to_angle)
from patrol.scene.render import RenderOptions, SceneRenderer
from patrol.scene.world import World

GK = dict(value=0.85, range_min=0.0, range_max=1.6, sweep_deg=270.0,
          zero_offset_deg=-135.0, major_ticks=9)


def gray_of(**kw):
    rgb = render_pointer_gauge(360, unit="MPa", normal_band=(0.4, 1.2), **kw)
    return cv2.cvtColor(rgb, cv2.COLOR_BGR2GRAY)


# ---------------------------------------------------------------- 贴图级
def test_needle_mask_lands_on_the_dark_needle():
    """**这一条是地基。**针的掩膜盖住的像素，在图上必须真的是墨色。

    比例常数（_R_NEEDLE / _R_FACE …）任何一处不同步，这里立刻红。
    """
    g = gray_of(**GK)
    m = render_pointer_gauge_mask(360, **GK)
    needle = g[m == SEG_LABELS["needle"]]
    face = g[m == SEG_LABELS["face"]]
    assert needle.size > 100 and face.size > 1000
    assert needle.mean() < 90, "针的掩膜落在了浅色像素上，平均灰度 %.1f" % needle.mean()
    assert face.mean() > 180, "表面的掩膜落在了深色像素上，平均灰度 %.1f" % face.mean()
    assert face.mean() - needle.mean() > 100


def test_ticks_are_a_separate_class_from_the_needle():
    """刻度线和指针一样是"从圆心往外的深色细长条"，是指针分割的头号干扰。

    显式分成两类，模型才有机会学会区分；合成一类等于把这个难点藏起来。
    """
    m = render_pointer_gauge_mask(360, **GK)
    assert (m == SEG_LABELS["ticks"]).sum() > 100
    assert (m == SEG_LABELS["needle"]).sum() > 50
    assert set(np.unique(m)) <= set(SEG_LABELS.values())


def test_mask_follows_the_value():
    """针的掩膜要跟着读数转。不转说明掩膜画的是别的东西。"""
    def tip_angle(value):
        m = render_pointer_gauge_mask(360, **dict(GK, value=value))
        ys, xs = np.where(m == SEG_LABELS["needle"])
        c = 179.5
        # 取离圆心最远的那个针像素当尖端
        r = (xs - c) ** 2 + (ys - c) ** 2
        i = int(np.argmax(r))
        return np.degrees(np.arctan2(ys[i] - c, xs[i] - c)) + 90.0

    lo, hi = tip_angle(0.2), tip_angle(1.4)
    want = value_to_angle(1.4, 0.0, 1.6, 270.0, -135.0) - \
        value_to_angle(0.2, 0.0, 1.6, 270.0, -135.0)
    # 角度是环量，差值要绕回 (-180, 180]，否则 202.5° 会显示成 -157.5°
    got = (hi - lo + 180.0) % 360.0 - 180.0
    want = (want + 180.0) % 360.0 - 180.0
    assert abs(got - want) < 8.0, "掩膜转了 %.1f°，读数变化对应 %.1f°" % (got, want)


def test_mask_has_no_interpolated_classes():
    """标签图不许有插值出来的中间值——0.5 不是一个类别。"""
    m = render_pointer_gauge_mask(200, **GK)
    assert m.dtype == np.uint8
    assert set(np.unique(m).tolist()) <= {0, 1, 2, 3}


# ---------------------------------------------------------------- 整帧级
@pytest.fixture(scope="module")
def framed():
    cfg = Config.load()
    r = SceneRenderer(World(cfg), RenderOptions(width=1280, height=720))
    return r.render(pose_xy_yaw=(12.43, -3.18, 0.0), pan_deg=90.0, tilt_deg=2.0,
                    zoom=3.0, want_mask=True)


def test_render_returns_two_values_by_default():
    """老调用点全都按两个返回值写的，加掩膜不能把它们打掉。"""
    cfg = Config.load()
    r = SceneRenderer(World(cfg), RenderOptions(width=640, height=360))
    got = r.render(pose_xy_yaw=(12.43, -3.18, 0.0), pan_deg=90.0, tilt_deg=2.0,
                   zoom=1.0)
    assert len(got) == 2


def test_mask_survives_the_perspective_warp(framed):
    """**斜看表盘时图上是椭圆**，掩膜要跟着一起变形，不能只是缩放。"""
    img, meta, mask = framed
    assert mask.shape == img.shape[:2] and mask.dtype == np.uint8
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    needle = g[mask == SEG_LABELS["needle"]]
    face = g[mask == SEG_LABELS["face"]]
    assert needle.size > 20, "整帧里一个针像素都没有"
    assert face.mean() - needle.mean() > 50, (
        "透视变换之后掩膜与图像错位了：针 %.1f，面 %.1f"
        % (needle.mean(), face.mean()))


def test_mask_uses_nearest_neighbour_only(framed):
    """线性插值会在"针"和"面"的交界插出 1.5 这种不存在的类别，
    而那正是决定角度的位置。"""
    _img, _meta, mask = framed
    assert set(np.unique(mask).tolist()) <= set(SEG_LABELS.values())


def test_mask_is_not_blurred_by_post_processing(framed):
    """掩膜不该跟着走运动模糊和噪声——那些是成像的失真，标签不是。"""
    _img, _meta, mask = framed
    ys, xs = np.where(mask == SEG_LABELS["needle"])
    if ys.size:
        # 硬边：针的连通域应当是实心的，不该有一圈过渡值
        sub = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        assert set(np.unique(sub).tolist()) <= set(SEG_LABELS.values())


def test_seg_names_match_labels():
    assert [SEG_NAMES[v] for v in sorted(SEG_LABELS.values())] == SEG_NAMES


# ---------------------------------------------------------------- 生成器
def test_generator_writes_a_usable_dataset(tmp_path):
    """跑一遍生成器，检查四种标注都落了盘且互相对得上号。"""
    from training.gen_synthetic import CLASSES, main
    out = tmp_path / "ds"
    assert main(["--n", "6", "--preview", "2", "--out", str(out),
                 "--width", "960", "--height", "540", "--seed", "3"]) == 0
    imgs = sorted((out / "images").rglob("*.jpg"))
    assert len(imgs) == 6
    for im in imgs:
        split = im.parent.name
        assert (out / "labels" / split / (im.stem + ".txt")).exists()
        assert (out / "masks" / split / (im.stem + ".png")).exists()
    assert (out / "data.yaml").exists() and (out / "ocr.jsonl").exists()
    txt = (out / "data.yaml").read_text(encoding="utf-8")
    assert "nc: %d" % len(CLASSES) in txt


def test_yolo_labels_are_normalised_and_in_range(tmp_path):
    """归一化写错（比如漏除以宽高）不会报错，只会让训练完全学不到东西。"""
    from training.gen_synthetic import main
    out = tmp_path / "ds"
    main(["--n", "5", "--preview", "0", "--out", str(out),
          "--width", "960", "--height", "540", "--seed", "1"])
    seen = 0
    for f in (out / "labels").rglob("*.txt"):
        for line in f.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            assert len(parts) == 5
            assert 0 <= int(parts[0]) < 5
            for v in parts[1:]:
                assert 0.0 <= float(v) <= 1.0, line
            seen += 1
    assert seen > 0, "一个标注框都没生成"


def test_l3_normal_set_contains_no_anomalies(tmp_path):
    """**非监督异常检测的正常集里混进一张异常样本就是给它下毒。**"""
    from patrol.common.config import Config as C
    from training.gen_synthetic import main
    out = tmp_path / "ds"
    main(["--n", "8", "--preview", "0", "--out", str(out),
          "--width", "960", "--height", "540", "--seed", "5"])
    world = World(C.load())
    bad = {t.id for t in world.targets if t.anomalous}
    for f in (out / "normal").glob("*.jpg"):
        tid = f.stem.split("_", 1)[1]
        assert tid not in bad, "正常集里混进了异常目标 %s" % tid


def test_ocr_labels_carry_the_text_actually_printed(tmp_path):
    """OCR 标注写的必须是渲染器真画上去的那几个字，不是想当然的。"""
    import json
    from training.gen_synthetic import main
    out = tmp_path / "ds"
    main(["--n", "10", "--preview", "0", "--out", str(out),
          "--width", "1280", "--height", "720", "--seed", "7"])
    rows = [json.loads(l) for l in
            (out / "ocr.jsonl").read_text(encoding="utf-8").splitlines() if l]
    if not rows:
        pytest.skip("这一批没采到够大的表盘")
    r = rows[0]
    lo, hi = r["range"]
    # 五个标签是量程四等分，与 gauges.render_pointer_gauge 的画法一致
    want = ["%g" % round(lo + i / 4.0 * (hi - lo), 2) for i in range(5)]
    assert r["texts"][:5] == want
    assert r["texts"][-1] == r["unit"]
