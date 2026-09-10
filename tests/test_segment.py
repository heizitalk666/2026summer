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


# ---------------------------------------------------------------- onnx 后端
#
# 这一节以前是空白：19 条用例里没有一行碰过 OnnxSegmenter。而乙的比选结论
# （「不启用学习法」）恰恰是拿 onnx 这条路跑出来的——CI 全绿并不能说明那条
# 路还跑得通，比选就成了纸上谈兵。
#
# 前两条不需要权重文件，任何人 clone 下来都能跑；后两条要权重，缺了自动 skip。
ONNX_W = "training/runs/seg/unet.onnx"


def _has_onnx_weights() -> bool:
    from pathlib import Path
    return Path(ONNX_W).exists()


def test_onnx_backend_with_npz_weights_fails_the_way_node_can_absorb(tmp_path):
    """backend 切 onnx 却忘了改 weights —— 必须抛 node.py 兜得住的类型。

    **这是最容易踩、后果最重的一次配错。**backend 和 weights 是两个字段，
    而 npz 与 onnx 共用后者；configs/system.yaml 里 weights 显式写着
    pixel.npz。所以"只把 backend 改成 onnx"会拿 onnxruntime 去加载 npz，
    而 `path.exists()` 拦不住——文件确实在。

    onnxruntime 原生抛的 InvalidProtobuf 既不是 ValueError 也不是
    FileNotFoundError，而 perception/node.py 只捕这两类。不转换类型的话，
    这个配错不是"warn 一句退回几何法"，是**整个 PerceptionNode 构造崩掉**。
    """
    pytest.importorskip("onnxruntime")
    from patrol.perception.segment.onnx_seg import OnnxSegmenter

    fake = tmp_path / "pixel.npz"
    np.savez(fake, W=np.zeros((N_FEAT, N_CLASS), np.float32))
    with pytest.raises((FileNotFoundError, ValueError)):
        OnnxSegmenter(Config.load(), weights=str(fake))


@pytest.mark.parametrize("size", [250, 300, 255])
def test_onnx_input_size_must_be_multiple_of_16(size, tmp_path):
    """input_size 不是 16 的倍数 —— 必须构造时就报，不能留到每帧静默失效。

    U-Net 四次 2× 下采样，跳连要求两边尺寸对得上。250 这类尺寸会让推理炸在
    Concat 上，而 segment() 是 `except: return None`，于是**每一帧都退回几何法，
    node 日志却还在说「分割级联已启用」**——正是本文件开篇点名的头号风险。
    """
    pytest.importorskip("onnxruntime")
    if not _has_onnx_weights():
        pytest.skip("需要 %s" % ONNX_W)
    from patrol.perception.segment.onnx_seg import OnnxSegmenter

    cfg = Config.load(overrides={
        "perception": {"segmenter": {"input_size": size}}})
    with pytest.raises(ValueError, match="16"):
        OnnxSegmenter(cfg, weights=ONNX_W)


def dial_with_mask(px=220, value=0.85):
    """与 dial() 逐像素对齐的真值掩膜。两者共用同一套几何参数（见 gauges.py）。"""
    import cv2
    img, box = dial(px, value=value)
    from patrol.scene.gauges import render_pointer_gauge_mask
    src = render_pointer_gauge_mask(512, value=value, range_min=0.0,
                                    range_max=1.6, sweep_deg=270.0,
                                    zero_offset_deg=-135.0, major_ticks=27)
    small = cv2.resize(src, (px, px), interpolation=cv2.INTER_NEAREST)
    return img, small, box


def _dir_of(sel):
    """一组布尔像素的方向合矢量：返回 (角度, R̄)。R̄ 接近 0 表示各向同性。"""
    if sel.sum() < 8:
        return None, 0.0
    h, w = sel.shape
    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    ang = np.arctan2(yy[sel] - cy, xx[sel] - cx)
    vx, vy = float(np.cos(ang).mean()), float(np.sin(ang).mean())
    return float(np.arctan2(vy, vx)), float(np.hypot(vx, vy))


def test_onnx_segmenter_channels_are_not_permuted():
    """通道语义必须和 SEG_LABELS 对齐 —— 错位不会报错，只会让读数悄悄变差。

    onnx_seg.py 按下标取：needle=p[...,2]、face=p[...,1]、ticks=p[...,3]。
    换一份类别顺序不同的权重进来，代码照跑、形状照对、掩膜照出，只是 needle
    里装的是刻度。**静态查不出来，只能靠行为反推。**

    两条判据都以 render_pointer_gauge_mask 的真值为参照——它和 dial() 用的是
    同一套几何常数，所以不需要在测试里重新推导角度约定（推错了会把好模型
    判成错位，实测踩过）：

    1. 真值针像素里，预测成 needle 的比例必须显著高于预测成 face / ticks 的
    2. 预测 needle 掩膜的方向必须跟着真值针的方向转；盘面与刻度是近似各向
       同性的，方向性应当明显更弱
    """
    pytest.importorskip("onnxruntime")
    if not _has_onnx_weights():
        pytest.skip("需要 %s" % ONNX_W)
    from patrol.perception.segment.onnx_seg import OnnxSegmenter

    seg = OnnxSegmenter(Config.load(), weights=ONNX_W)
    hit, dirs = [], []
    for value in (0.24, 0.56, 0.88, 1.20, 1.52):
        img, gt, box = dial_with_mask(220, value=value)
        x0, y0, x1, y1 = box
        m = seg.segment(img[y0:y1, x0:x1])
        assert m is not None and m.needle.shape == gt.shape

        # 判据 1：真值针像素落到哪个预测通道
        stack = np.stack([m.face, m.needle, m.ticks], axis=-1)   # 1 / 2 / 3
        pick = stack.argmax(axis=-1)
        tn = gt == 2
        assert tn.sum() >= 8, "真值掩膜里没有针，value=%.2f" % value
        frac = [float((pick[tn] == i).mean()) for i in range(3)]
        hit.append((value, frac))

        # 判据 2：方向性
        want, _ = _dir_of(tn)
        got, rbar = _dir_of(m.needle > 0.5)
        other = [_dir_of(c > 0.5)[1] for c in (m.face, m.ticks)]
        d = None if (want is None or got is None) else             abs((got - want + np.pi) % (2 * np.pi) - np.pi)
        dirs.append((value, None if d is None else float(np.rad2deg(d)),
                     rbar, other))

    # 1：真值针像素预测成 needle 的比例，必须超过 face 与 ticks
    for value, frac in hit:
        assert frac[1] > frac[0] and frac[1] > frac[2], (
            "value=%.2f 真值针像素落进 face/needle/ticks 的比例是 %r，"
            "needle 不是最高——通道疑似错位" % (value, frac))

    # 2：方向性。**只在针掩膜本身有方向可言时才判**——这份权重的针召回只有
    # 0.4 上下（见 docs/指标汇总表.md 的 L2 一节），个别角度给出的是一团接近
    # 各向同性的弱响应，R̄ 掉到 0.1。对那种情况谈「指向」没有意义，硬判会让这条
    # 用例随权重版本随机翻红，而它本来要抓的是通道错位，不是精度。
    #
    # 阈值取 60° 而不是 30°：错位时的表现是 face 约 84°、ticks 约 127°（实测），
    # 60° 能把「对」和「错位」干净分开，同时不把这份权重的松散判成错位。
    strong = [(v, d, r, o) for v, d, r, o in dirs if d is not None and r >= 0.25]
    assert len(strong) >= 3, (
        "针通道在多数角度上弱到没有方向性，无法判错位：%r" % (dirs,))
    med = float(np.median([d for _v, d, _r, _o in strong]))
    assert med < 60.0, "针通道指向与真值差 %.1f°，通道可能错位：%r" % (med, dirs)
    for _v, _d, rbar, other in strong:
        assert rbar > max(other), (
            "针通道方向性 %.3f 不强于盘面/刻度 %r，疑似错位" % (rbar, other))


def test_onnx_cascade_actually_drives_the_reading():
    """级联必须真的在用 —— 「不抛异常 + 形状对」远远不够。

    input_size=300 那个 case 正是全部满足"不抛异常、掩膜形状对、值域在
    [0,1]"却每帧静默失效。唯一能钉死的判据是读数管线自己报的 seg_used。
    """
    pytest.importorskip("onnxruntime")
    if not _has_onnx_weights():
        pytest.skip("需要 %s" % ONNX_W)
    from patrol.perception.segment.onnx_seg import OnnxSegmenter

    seg = OnnxSegmenter(Config.load(), weights=ONNX_W)
    used = []
    for value in (0.40, 0.85, 1.30):
        img, box = dial(220, value=value)
        r = read_pointer_gauge(img, box, priors=PRIORS, segmenter=seg,
                               want_debug=True)
        used.append(bool((r.debug or {}).get("seg_used")))
    assert all(used), "掩膜没有驱动读数，级联是空转的：%r" % (used,)
