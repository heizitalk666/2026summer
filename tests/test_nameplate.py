"""表面文字的语义解析与互证。

**这一层最容易写出"看起来很聪明、实际上是误报源"的代码**，所以用例分成两组，
两组同等重要：

- 真事故必须报出来（看错表、量纲错）
- 真噪声必须不报（小数点丢失、半对半错的识别结果）

第二组是实测出来的。一块 120 px 的表，RapidOCR 会把 0.4 读成 4、1.2 读成 12、
把 MPa 读成 kPa。第一版按"有数字对不上就翻脸"写，端到端复核成功率从 100 %
直接掉到 28.6 %——互证通路自己成了系统里最大的误报源。
"""
from __future__ import annotations

import pytest

from patrol.perception.ocr.base import OcrLine
from patrol.perception.reading.nameplate import (canon_switch_state,
                                                 cross_check_dial,
                                                 implied_range, parse_dial_text,
                                                 parse_number,
                                                 read_digital_value,
                                                 read_switch_text)

MPA = {"range_min": 0.0, "range_max": 1.6, "unit": "MPa"}


def lines(*items) -> list[OcrLine]:
    """(文本, 置信度) 或纯文本（默认 0.95）。"""
    out = []
    for it in items:
        t, c = it if isinstance(it, tuple) else (it, 0.95)
        out.append(OcrLine(t, c, (0.0, 0.0, 10.0, 10.0)))
    return out


def agree(*items, priors=None):
    return cross_check_dial(parse_dial_text(lines(*items)), priors or MPA)


# ---------------------------------------------------------------- 词条解析
@pytest.mark.parametrize("text,want", [
    ("0.8", 0.8), ("1.6", 1.6), ("0", 0.0),
    ("1.6.", 1.6),          # 尾随标点，实测常见
    ("1.6MPa", 1.6),        # 数字与单位粘连
    ("O.4", 0.4),           # O/0 形近
    ("MPa", None), ("", None), ("abc", None), ("--", None),
])
def test_parse_number(text, want):
    assert parse_number(text) == want


def test_unit_and_numbers_are_separated():
    d = parse_dial_text(lines("0", "0.4", "0.8", "1.2", "1.6", "MPa"))
    assert d.numbers == [0.0, 0.4, 0.8, 1.2, 1.6] and d.unit == "MPa"


def test_number_confidence_is_averaged_over_numeric_tokens_only():
    """单位那一条的置信度不该混进数字的均置信里——它俩是分开用的。"""
    d = parse_dial_text(lines(("0.4", 0.6), ("0.8", 0.6), ("MPa", 1.0)))
    assert d.number_conf == pytest.approx(0.6, abs=1e-6)
    assert d.unit_conf == pytest.approx(1.0)


# ---------------------------------------------------------------- 真事故
def test_wrong_gauge_is_caught():
    """先验说 0–1.6，表面印的是 0–6：车看错了表，或标定表配错了航点。

    这类错误几何法**原理上**发现不了：指针角度量得再准，套错量程算出来的
    读数照样是一个看起来完全正常的值。
    """
    c = agree("0", "1.5", "3", "4.5", "6")
    assert c.agree is False and "不是同一块表" in c.detail


def test_wrong_gauge_is_still_caught_when_ocr_drops_the_decimal_points():
    """OCR 同时丢了小数点也不能把真事故盖过去：0/15/30/45/60 依然自成一套刻度。"""
    assert agree("0", "15", "30", "45", "60").agree is False


def test_unit_across_physical_quantities_is_a_hard_conflict():
    """一块压力表不可能标成 A。这条不需要任何数字佐证。"""
    c = cross_check_dial(parse_dial_text(lines("MPa")),
                         {"range_min": 0.0, "range_max": 64.0, "unit": "A"})
    assert c.agree is False and "跨物理量" in c.detail


# ---------------------------------------------------------------- 真噪声
def test_decimal_dropout_is_not_a_conflict():
    """**实测的头号失效模式。**0.4→4、1.2→12、1.6→16，小数点第一个丢。

    整批除以 10 精确落回先验刻度，这种巧合不可能是偶然——是同一块表。
    """
    c = agree("0", "12", "16")
    assert c.agree is True and "小数点" in c.detail


def test_full_decimal_dropout_set_is_recognised():
    assert agree("0", "4", "8", "12", "16").agree is True


def test_garbled_numbers_are_insufficient_evidence_not_conflict():
    """半对半错的识别结果凑不出一套自洽刻度，判"读不清楚"而不是"看错表"。

    这两个取值是实测抓到的（120 px 的表盘）。判成冲突的代价是：好表被判
    标定错配、复核预算白烧一次、一条工单推到人面前。
    """
    for items in (("0.8", "2.0", "2.4", "10"), ("1.6", "12", "88")):
        c = cross_check_dial(parse_dial_text(lines(*items)), MPA)
        assert c.agree is None, "%s 被判成了冲突：%s" % (items, c.detail)


def test_si_prefix_confusion_is_downgraded_to_noise():
    """MPa/kPa 只差一个字母，小字号下必然混淆，不足以据此翻脸。"""
    c = cross_check_dial(parse_dial_text(lines("0.4", "1.2", "kPa")), MPA)
    assert c.agree is None and "前缀" in c.detail


def test_a_single_garbage_number_never_triggers_a_conflict():
    assert agree(("14", 0.6)).agree is not False


def test_low_confidence_numbers_do_not_trigger_a_conflict():
    c = agree(("0", 0.4), ("1.5", 0.4), ("3", 0.4), ("4.5", 0.4), ("6", 0.4))
    assert c.agree is not False, "低置信度的识别结果不该作为翻脸依据"


# ---------------------------------------------------------------- 一致
def test_correct_dial_agrees():
    c = agree("0", "0.4", "0.8", "1.2", "1.6", "MPa")
    assert c.agree is True and c.unit_agree is True


def test_a_dial_labelled_every_tenth_still_agrees():
    """判据不许依赖"每 1/4 量程标一个数"这种只对本项目渲染器成立的假设。"""
    items = ["%g" % (0.16 * i) for i in range(11)]
    assert cross_check_dial(parse_dial_text(lines(*items)), MPA).agree is True


def test_no_priors_means_no_cross_check_ran():
    c = cross_check_dial(parse_dial_text(lines("0.4")), None)
    assert c.ran is False and c.agree is None


def test_no_text_means_no_cross_check_ran():
    """OCR 什么都没读到，是"这一路缺席"，不是"这一路说没问题"。"""
    c = cross_check_dial(parse_dial_text([]), MPA)
    assert c.ran is False and c.agree is None


# ---------------------------------------------------------------- 自洽刻度
def test_implied_range_rejects_degenerate_lattices():
    """任取几个数，让最小间隔去整除其余间隔，几乎总能凑出一个网格。

    没有跨度上限的话 [0.8, 2.0, 2.4, 10.0] 会被认成"公差 0.4 的 23 格刻度"，
    于是每一批噪声都能指认出一块并不存在的表。
    """
    assert implied_range([0.8, 2.0, 2.4, 10.0]) is None
    assert implied_range([0.0, 1.5, 3.0, 4.5, 6.0]) == (0.0, 6.0)


def test_implied_range_needs_three_numbers():
    assert implied_range([0.0, 6.0]) is None


# ---------------------------------------------------------------- 离散量
@pytest.mark.parametrize("text,want", [
    ("CLOSED", "ON"), ("OPEN", "OFF"), ("ON", "ON"), ("OFF", "OFF"),
    ("合闸", "ON"), ("分闸", "OFF"), ("on", "ON"), ("Off.", "OFF"),
    (None, None), ("???", None),
])
def test_switch_state_is_canonicalised(text, want):
    """**两条通路用的是两套词。**几何法报 CLOSED/OPEN，位置牌印 ON/OFF。

    第一版直接拿字符串比对，于是"几何法 CLOSED vs 位置牌 ON"——两路其实
    完全一致——被判成互相矛盾，一轮里所有开关证据包都被误标成需要人工复核。
    """
    assert canon_switch_state(text) == want


def test_switch_plate_is_read_with_confidence():
    assert read_switch_text(lines(("OFF", 0.93))) == ("OFF", 0.93)
    assert read_switch_text(lines("量程")) == (None, 0.0)


def test_digital_value_picks_the_tallest_number_not_the_most_confident():
    """数显屏上印刷体的型号比七段码好认，按置信度挑会挑到型号。"""
    small = OcrLine("2024", 0.99, (0.0, 0.0, 20.0, 8.0))     # 型号小字
    big = OcrLine("36.5", 0.80, (0.0, 20.0, 60.0, 60.0))     # 读数大字
    assert read_digital_value([small, big]) == (36.5, 0.80)
