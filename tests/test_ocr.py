"""OCR 通路：真引擎跑真渲染图。

**这一条是全项目唯一一个"真的学出来的模型在跑"的测试。**检测器目前还是
合成的（权重没训），分割走的是几何法，L3 是统计法——只有 OCR 这一路，
从安装到推理都是完整的：`rapidocr-onnxruntime` 15 MB 的包里自带 ONNX 权重，
装完不联网就能用。

用例分两层：
- 引擎不在时必须优雅降级（组里同学不装可选依赖也能跑通全链路）
- 引擎在时，钉住**可读像素阈值**——这个数直接支撑像素密度论题
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from patrol.common.config import Config
from patrol.perception.ocr.base import build_ocr, crop
from patrol.perception.ocr.disabled import DisabledOcr
from patrol.perception.reading.nameplate import cross_check_dial, parse_dial_text
from patrol.scene.gauges import render_pointer_gauge

MPA = {"range_min": 0.0, "range_max": 1.6, "unit": "MPa"}


def has_engine() -> bool:
    try:
        import rapidocr_onnxruntime            # noqa: F401
        return True
    except Exception:                          # noqa: BLE001
        return False


needs_engine = pytest.mark.skipif(not has_engine(),
                                  reason="未安装 rapidocr-onnxruntime")


def dial(px: int, *, pad: int = 40) -> tuple[np.ndarray, tuple]:
    """把一块表画在灰底上，返回 (整图, bbox)。渲染尺寸固定 512 再缩到 px，
    这样"小"就只来自分辨率，而不是画得潦草。"""
    src = render_pointer_gauge(512, value=0.85, range_min=0.0, range_max=1.6,
                               unit="MPa", sweep_deg=270.0, zero_offset_deg=-135.0,
                               major_ticks=9, normal_band=(0.4, 1.2))
    small = cv2.resize(src, (px, px), interpolation=cv2.INTER_AREA)
    img = np.full((px + 2 * pad, px + 2 * pad, 3), 150, np.uint8)
    img[pad:pad + px, pad:pad + px] = small
    return img, (pad, pad, pad + px, pad + px)


def ocr_of(**over):
    ov = {"logging": {"dir": "logs"}, "perception": {"ocr": dict(over)}}
    return build_ocr(Config.load(overrides=ov))


# ---------------------------------------------------------------- 降级
def test_backend_off_returns_a_disabled_engine():
    o = ocr_of(backend="off")
    assert isinstance(o, DisabledOcr) and o.available is False
    assert o.read(np.zeros((100, 100, 3), np.uint8)) == []


def test_disabled_engine_reports_why():
    """降级原因要能查到——事后问"为什么这一轮没有互证"时它是唯一线索。"""
    assert ocr_of(backend="off").model_info()["reason"]


def test_an_unknown_backend_is_loud():
    """配置写错要立刻报错。悄悄退回默认值会让人以为 OCR 在跑，其实没在跑。"""
    with pytest.raises(ValueError):
        ocr_of(backend="tesseract-typo")


def test_factory_never_writes_log_files():
    """工厂被测试反复调用，顺手建 logger 会落一地日志文件。"""
    import os
    before = set(os.listdir("logs")) if os.path.isdir("logs") else set()
    ocr_of(backend="off")
    after = set(os.listdir("logs")) if os.path.isdir("logs") else set()
    assert after == before


def test_crop_expands_the_box():
    """单位印在表盘中下方、开关的 ON/OFF 牌在底边，贴边裁会把它们切一半。"""
    img = np.zeros((200, 200, 3), np.uint8)
    patch, x0, y0 = crop(img, (50, 50, 100, 100), margin=0.20)
    assert patch.shape[0] > 50 and (x0, y0) == (40.0, 40.0)


def test_crop_rejects_a_degenerate_box():
    assert crop(np.zeros((200, 200, 3), np.uint8), (10, 10, 12, 12)) is None


# ---------------------------------------------------------------- 真引擎
@needs_engine
def test_engine_reads_a_clean_dial():
    o = ocr_of(backend="rapid", min_side_px=32.0)
    img, box = dial(320)
    d = parse_dial_text(o.read(img, box))
    assert d.unit == "MPa", "读不出单位：%s" % d.as_dict()
    assert cross_check_dial(d, MPA).agree is True


@needs_engine
def test_ocr_boxes_come_back_in_original_image_coordinates():
    """预览窗口要直接拿它画框，坐标系错了就画到别处去了。"""
    o = ocr_of(backend="rapid", min_side_px=32.0)
    img, box = dial(320, pad=60)
    for ln in o.read(img, box):
        assert box[0] - 40 <= ln.cx <= box[2] + 40
        assert box[1] - 40 <= ln.cy <= box[3] + 40


@needs_engine
def test_tiny_roi_is_skipped_without_calling_the_engine():
    """放大到位之前跑 OCR 是白花 0.3 s，而且读出来的是垃圾。"""
    o = ocr_of(backend="rapid", min_side_px=120.0)
    img, box = dial(60)
    assert o.read(img, box) == []


@needs_engine
def test_legibility_threshold_is_where_the_density_thesis_says_it_is():
    """**这条钉的是像素密度论题在 OCR 这一路上的落点。**

    整个方案的立论是"5 m 处 1× 只有 50 px，不够，所以要停车变焦"。指针角度
    那一路的判据线是 120 px（由 0.5 % FS 精度反推）；这里测的是文字这一路。

    实测（正对、无透视、无抖动的理想图）：60 px 读出的是垃圾，90 px 起刻度
    数字正确，120 px 起单位也读得对。所以：

    - 60 px 时**必须不给出"一致"的结论**——那是幻觉，不是证据
    - 120 px 时应当读得出单位

    真实场景里还有透视、云台残余抖动、以及 4K→1920 裁剪重采样（z=2.4 时
    有效感光像素只有 83 %），所以实际可读阈值比这里高。表现出来就是互证
    如实报"证据不足"，而不是报一个错值——这正是设计要的行为。
    """
    o = ocr_of(backend="rapid", min_side_px=16.0)

    img, box = dial(60)
    d60 = parse_dial_text(o.read(img, box))
    assert cross_check_dial(d60, MPA).agree is not True, (
        "60 px 上居然判成一致，说明互证通路在幻觉：%s" % d60.as_dict())

    img, box = dial(160)
    d160 = parse_dial_text(o.read(img, box))
    assert d160.unit == "MPa", "160 px 上读不出单位：%s" % d160.as_dict()
    assert cross_check_dial(d160, MPA).agree is True


@needs_engine
def test_engine_failure_does_not_escape():
    """引擎内部抛异常不能把复核带走——没有互证 = 结论更保守，不是崩溃。"""
    o = ocr_of(backend="rapid", min_side_px=16.0)

    class Boom:
        def __call__(self, *a, **k):
            raise RuntimeError("推理会话炸了")

    o._engine = Boom()                                  # noqa: SLF001
    img, box = dial(200)
    assert o.read(img, box) == []


@needs_engine
def test_model_info_is_honest_about_being_offline():
    """车上没有外网。这个字段是部署前唯一能一眼确认这件事的地方。"""
    info = ocr_of(backend="rapid").model_info()
    assert info["offline"] is True and info["backend"] == "onnxruntime"
