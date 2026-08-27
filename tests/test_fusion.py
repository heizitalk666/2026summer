"""L4 决策融合：四路模型说完话之后由谁拍板。

这一层没有权重，全是规则——**所以它必须每一条都被钉住**。学出来的融合网络
错了还能说"再训一版"，规则错了就是逻辑错，而且会稳定地错在同一个地方。

六种结论（`verdict.result` 的六个枚举值）每个至少一条用例，外加三条最容易
写反的优先级：读数压过 L3、互证冲突压过读数、证据不足报不足而不是报冲突。
"""
from __future__ import annotations

import pytest

from patrol.perception.fusion import (CONFIRM_THR, Evidence, RESULTS,
                                      corroboration, fuse)
from patrol.perception.reading.nameplate import CrossCheck


def gauge(value=0.42, band=True, conf=0.9, kind="POINTER_GAUGE"):
    return {"kind": kind, "value": value, "unit": "MPa", "range_min": 0.0,
            "range_max": 1.6, "in_normal_band": band,
            "reading_confidence": conf, "roi": [0.0, 0.0, 50.0, 50.0]}


def cc(agree, detail="…"):
    return CrossCheck(ran=True, agree=agree, detail=detail)


def ev(**kw):
    base = dict(defect_class="PRESSURE_GAUGE", conf_before=0.45, conf_after=0.92,
                pixel_density_px=120.0, density_target_px=120.0)
    base.update(kw)
    return Evidence(**base)


# ---------------------------------------------------------------- 六种结论
def test_reading_ok():
    r = fuse(ev(l2=gauge(band=True), cross=cc(True)))
    assert r.result == "READING_OK" and r.severity == "INFO"
    assert r.needs_human_review is False


def test_reading_abnormal():
    r = fuse(ev(l2=gauge(value=1.45, band=False), cross=cc(True)))
    assert r.result == "READING_ABNORMAL" and r.severity == "WARN"
    # 出带是明确的告警，不需要人来确认——需要人确认的是"说不清"的那些
    assert r.needs_human_review is False


def test_unknown_anomaly():
    r = fuse(ev(defect_class="FOREIGN_OBJECT", conf_after=0.5, l2=None,
                is_anomaly=True, anomaly_score=0.71))
    assert r.result == "UNKNOWN_ANOMALY" and r.needs_human_review is True
    # L3 不认识的东西谈不上类别，defect_class 必须留空而不是沿用一级的猜测
    assert r.verdict()["defect_class"] is None


def test_confirmed_defect():
    r = fuse(ev(defect_class="OIL_LEAK", conf_before=0.4, conf_after=0.93))
    assert r.result == "CONFIRMED_DEFECT" and r.severity == "CRITICAL"


def test_confirmed_defect_is_warn_when_confidence_is_moderate():
    r = fuse(ev(defect_class="RUST_CORROSION", conf_before=0.5, conf_after=0.65))
    assert r.result == "CONFIRMED_DEFECT" and r.severity == "WARN"


def test_false_alarm_is_a_result_not_a_failure():
    """一级为保召回压到 0.25 必然带来误报，复核把它们消解掉正是立论所在。"""
    r = fuse(ev(conf_before=0.41, conf_after=0.05))
    assert r.result == "FALSE_ALARM" and r.severity == "INFO"
    assert r.verdict()["defect_class"] is None


def test_inconclusive_when_nothing_is_conclusive():
    r = fuse(ev(conf_before=0.40, conf_after=0.38, l2=None))
    assert r.result == "INCONCLUSIVE" and r.needs_human_review is True


def test_every_result_value_is_in_the_frozen_enum():
    assert set(RESULTS) == {"CONFIRMED_DEFECT", "FALSE_ALARM", "READING_OK",
                            "READING_ABNORMAL", "UNKNOWN_ANOMALY", "INCONCLUSIVE"}


# ---------------------------------------------------------------- 优先级
def test_reading_wins_over_l3():
    """**这条排错了，一整轮压力表全会被判成 UNKNOWN_ANOMALY，读数通路白做。**

    L3 是非监督的，只学过"看起来正常"，对一块读数明确且在带内的表报异常
    多半是光照或视角变化引起的重构误差。
    """
    r = fuse(ev(l2=gauge(band=True), is_anomaly=True, anomaly_score=0.8))
    assert r.result == "READING_OK"
    # 但 L3 的意见不丢：ICD §3.1 规定它不得直接告警，只能进人工复核队列
    assert r.needs_human_review is True


def test_cross_check_conflict_wins_over_a_perfectly_normal_reading():
    """读数数值完全正常，但表面印的量程对不上先验——这个数是错的。

    错得没有征兆才是最危险的：报出去没人会去查。
    """
    r = fuse(ev(l2=gauge(band=True), cross=cc(False, "隐含量程 [0, 6]")))
    assert r.result == "INCONCLUSIVE" and r.needs_human_review is True


def test_insufficient_cross_check_evidence_is_not_a_conflict():
    """**证据不足 ≠ 冲突。**混为一谈时实测复核成功率 100 % → 28.6 %。"""
    r = fuse(ev(l2=gauge(band=True), cross=cc(None, "读不清楚")))
    assert r.result == "READING_OK"


def test_abort_short_circuits_everything():
    r = fuse(ev(l2=gauge(band=True), cross=cc(True), aborted=True))
    assert r.result == "INCONCLUSIVE" and r.needs_human_review is True


def test_reading_conclusions_require_the_density_line():
    """像素密度没到判据线就谈读数精度是自欺欺人（0.5 % FS 需要 120 px）。"""
    r = fuse(ev(l2=gauge(band=True), pixel_density_px=60.0))
    assert r.result == "INCONCLUSIVE"
    assert "像素密度" in " ".join(r.reasons)


def test_density_gate_does_not_block_non_reading_defects():
    """渗油、异物这类缺陷本来就不靠像素密度，别被读数的门槛误伤。"""
    r = fuse(ev(defect_class="OIL_LEAK", l2=None, conf_after=0.9,
                pixel_density_px=40.0))
    assert r.result == "CONFIRMED_DEFECT"


# ---------------------------------------------------------------- 开关两路
def test_switch_two_paths_agree_across_two_vocabularies():
    """几何法说 CLOSED、位置牌印 ON，说的是同一件事。

    比对前不归一的话，一致会被判成矛盾——实测一轮里 6 个开关证据包全中招。
    """
    e = ev(defect_class="SWITCH_HANDLE",
           l2=gauge(value="CLOSED", kind="SWITCH_POSITION"), ocr_state="ON")
    assert corroboration(e)[0] == "agree"
    assert fuse(e).result == "READING_OK"


def test_switch_two_paths_conflict_goes_to_a_human():
    e = ev(defect_class="SWITCH_HANDLE",
           l2=gauge(value="OPEN", band=False, kind="SWITCH_POSITION"),
           ocr_state="ON")
    r = fuse(e)
    assert corroboration(e)[0] == "conflict"
    assert r.result == "INCONCLUSIVE" and r.needs_human_review is True


def test_switch_without_a_plate_reading_is_absent_not_conflict():
    e = ev(defect_class="SWITCH_HANDLE",
           l2=gauge(value="CLOSED", kind="SWITCH_POSITION"), ocr_state=None)
    assert corroboration(e)[0] == "absent"
    assert fuse(e).result == "READING_OK"


# ---------------------------------------------------------------- 置信度
def test_corroboration_raises_confidence_and_conflict_halves_it():
    lo = fuse(ev(l2=gauge(), cross=cc(None))).confidence
    hi = fuse(ev(l2=gauge(), cross=cc(True))).confidence
    bad = fuse(ev(l2=gauge(), cross=cc(False))).confidence
    assert bad < lo < hi <= 1.0


def test_missing_second_path_is_slightly_conservative():
    """缺一路证据要略微保守，但不能保守到把结论翻掉。"""
    with_ = fuse(ev(l2=gauge(), cross=cc(None))).confidence
    without = fuse(ev(l2=gauge(), cross=None)).confidence
    assert without < with_


def test_confidence_stays_in_range():
    for c_after in (0.0, 0.3, 0.99, 1.0):
        r = fuse(ev(conf_after=c_after, l2=gauge(conf=1.0), cross=cc(True)))
        assert 0.0 <= r.confidence <= 1.0


# ---------------------------------------------------------------- 可解释
def test_every_conclusion_carries_its_reasoning():
    """**评审第一个问题一定是"凭什么"。**没有 reasons 的结论不算结论。"""
    for e in (ev(l2=gauge()), ev(l2=None, is_anomaly=True), ev(aborted=True),
              ev(conf_before=0.4, conf_after=0.02)):
        r = fuse(e)
        assert r.reasons and all(isinstance(x, str) and x for x in r.reasons)
        assert r.evidence["l1"] and "observation" in r.evidence


def test_verdict_only_carries_schema_allowed_fields():
    """verdict 是 additionalProperties: false，塞推理进去整份 manifest 就废了。"""
    v = fuse(ev(l2=gauge(), cross=cc(True))).verdict()
    assert set(v) == {"result", "defect_class", "severity",
                      "needs_human_review", "confidence"}


def test_fusion_verdict_passes_schema_validation():
    from patrol.common import messages as M
    v = fuse(ev(l2=gauge(), cross=cc(True))).verdict()
    M.validate(M.build_evidence_package(
        run_id="20260901-093012-a7f3",
        event_id="aaaa1111-0000-4000-8000-000000000001",
        waypoint_id="WP-07", ts_utc_ms=1_700_000_000_000, verdict=v,
        before=M.snapshot(confidence=0.45, pixel_density_px=49.9, zoom=1.0,
                          est_distance_m=5.06, defect_class="PRESSURE_GAUGE",
                          l2_reading=None),
        after=M.snapshot(confidence=0.92, pixel_density_px=119.8, zoom=2.4,
                         est_distance_m=5.06, defect_class="PRESSURE_GAUGE",
                         l2_reading=None),
        gain={"delta_conf": 0.47, "pixel_density_ratio": 2.4,
              "verify_success": True},
        timeline=[],
        files=[{"path": "cruise.jpg", "role": "CRUISE_ANNOTATED", "bytes": 16,
                "sha256": "0" * 64, "uploaded": False}],
        abort=None))
