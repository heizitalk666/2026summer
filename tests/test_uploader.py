"""证据包组装与复核结论。ICD §6。

三件事在这里钉住：

1. **before / after 必须描述同一个东西。**before 是巡航态广角端那一帧，
   after 是复核态变焦后那一帧，两者结构相同、类别相同，做差才有意义。
2. **判决优先级。**有二级读数时以读数为准，L3 排在它后面——L3 是非监督的，
   对一块读数明确且在正常带内的表报异常多半是光照或视角引起的重构误差。
3. **增益统计必须按 verdict 分组。**FALSE_ALARM 的 Δconf 是负值，混在一起
   算均值会接近零、看上去像复核没起作用（ICD §6.4 特意警告过）。
"""
from __future__ import annotations

import json

import pytest

from patrol.common.config import Config
from patrol.uploader.node import UploaderNode
from patrol.uploader.packer import decide_verdict


def snap(conf=0.45, p=49.9, zoom=1.0, dist=5.06, cls="PRESSURE_GAUGE", l2=None):
    return {"confidence": conf, "pixel_density_px": p, "zoom": zoom,
            "est_distance_m": dist, "defect_class": cls, "l2_reading": l2}


def reading(value=0.42, in_band=True):
    return {"kind": "POINTER_GAUGE", "value": value, "unit": "MPa",
            "range_min": 0.0, "range_max": 1.6, "in_normal_band": in_band,
            "reading_confidence": 0.9, "roi": [0.0, 0.0, 50.0, 50.0]}


# ---------------------------------------------------------------- 判决
def test_reading_wins_over_l3_anomaly():
    """有读数就以读数为准。**这条排错了整轮压力表都会被判成 UNKNOWN_ANOMALY。**"""
    v = decide_verdict(reading(in_band=True), before_conf=0.45, after_conf=0.92,
                       defect_class="PRESSURE_GAUGE", is_anomaly=True, aborted=False)
    assert v["result"] == "READING_OK"
    # L3 的意见不丢弃，转成人工复核标记（ICD §3.1：L3 不得直接告警）
    assert v["needs_human_review"] is True


def test_reading_out_of_band_is_an_alarm_without_human_review():
    v = decide_verdict(reading(value=1.45, in_band=False), before_conf=0.45,
                       after_conf=0.9, defect_class="PRESSURE_GAUGE",
                       is_anomaly=False, aborted=False)
    assert v["result"] == "READING_ABNORMAL" and v["needs_human_review"] is False


def test_l3_only_fires_when_there_is_no_reading():
    v = decide_verdict(None, before_conf=0.4, after_conf=0.5,
                       defect_class="FOREIGN_OBJECT", is_anomaly=True, aborted=False)
    assert v["result"] == "UNKNOWN_ANOMALY" and v["needs_human_review"] is True


def test_false_alarm_is_a_result_not_a_failure():
    """一级为保召回压到 0.25 必然带来误报，复核把它们消解掉正是立论所在。"""
    v = decide_verdict(None, before_conf=0.41, after_conf=0.05,
                       defect_class="PRESSURE_GAUGE", is_anomaly=False, aborted=False)
    assert v["result"] == "FALSE_ALARM" and v["severity"] == "INFO"


def test_abort_short_circuits_everything():
    v = decide_verdict(reading(), before_conf=0.4, after_conf=0.9,
                       defect_class="PRESSURE_GAUGE", is_anomaly=False, aborted=True)
    assert v["result"] == "INCONCLUSIVE" and v["needs_human_review"] is True


# ---------------------------------------------------------------- 配对
class _Bus:
    """假的 Subscriber，让 UploaderNode 不用真起 ZeroMQ 也能测。"""

    def drain(self, max_n=0):
        return []

    def close(self):
        pass


@pytest.fixture()
def node(tmp_path, monkeypatch):
    cfg = Config.load(overrides={
        "logging": {"dir": str(tmp_path / "logs")},
        "uploader": {"evidence_dir": str(tmp_path / "evidence"),
                     "upload_period_s": 1e6},
    })
    monkeypatch.setattr("patrol.uploader.node.Subscriber", lambda *a, **k: _Bus())
    n = UploaderNode(cfg)
    n.queue.transport = type("T", (), {
        "send_manifest": staticmethod(lambda m: True),
        "put_file": staticmethod(lambda *a, **k: True),
        "close": staticmethod(lambda: None)})()
    yield n
    n.close()


def det_event(*, event_id, stage, conf, p, zoom, cls="PRESSURE_GAUGE",
              track_id=7, l2=None, l3=None):
    return {
        "msg_type": "DETECTION_EVENT", "run_id": "20260901-093012-a7f3",
        "event_id": event_id, "stage": stage,
        "context": {"waypoint_id": "WP-07",
                    "ptz": {"pan_deg": 90.0, "tilt_deg": 2.0, "zoom": zoom,
                            "hfov_deg": 60.0 / zoom}},
        "detections": [{"track_id": track_id, "defect_class": cls,
                        "confidence": conf, "bbox": [0.0, 0.0, p, p],
                        "pixel_density_px": p, "est_distance_m": 5.06,
                        "l2_reading": l2}],
        "suspect": {"is_suspect": stage == "CRUISE", "target_track_id": track_id,
                    "trigger_rule": "L2_UNREADABLE", "severity": 0.7,
                    "novelty": 1.0, "priority": 0.3, "suppressed_by": None},
        "l3_anomaly": l3,
    }


def test_before_after_pairing_produces_real_gain(node):
    eid = "3f2b9c14-7d5e-4a81-b0c6-2e9f1a4d8e77"
    node.on_detection(det_event(event_id=eid, stage="CRUISE", conf=0.45,
                                p=49.9, zoom=1.0))
    node.on_detection(det_event(event_id=eid, stage="VERIFY", conf=0.91,
                                p=119.8, zoom=2.4, l2=reading()))
    m = json.loads((node.packer.dir_for(node.run_id, eid) / "manifest.json")
                   .read_text(encoding="utf-8"))
    assert m["gain"]["pixel_density_ratio"] == pytest.approx(119.8 / 49.9, rel=1e-3)
    assert m["gain"]["delta_conf"] == pytest.approx(0.46, abs=1e-6)
    assert m["gain"]["verify_success"] is True
    assert m["verdict"]["result"] == "READING_OK"


def test_before_only_accepts_a_cruise_stage_frame(node):
    """before 必须是巡航态广角端那一帧，三项增益全是拿它做基准算的。

    收下一帧变焦后的当 before，像素密度比会变成 1.0 左右，把复核最核心的
    那条指标做废——实测一轮八个证据包里有四个这么废掉过。
    """
    eid = "aaaaaaaa-0000-4000-8000-000000000001"
    node.on_detection(det_event(event_id=eid, stage="VERIFY", conf=0.9,
                                p=120.0, zoom=2.4))
    p = node.pending.get(eid)
    assert p is None or p.before is None


def test_after_prefers_the_same_defect_class(node):
    """变焦后跟踪常常重新分配 id；退回 detections[0] 会配错目标。"""
    eid = "bbbbbbbb-0000-4000-8000-000000000002"
    node.on_detection(det_event(event_id=eid, stage="CRUISE", conf=0.45,
                                p=49.9, zoom=1.0, cls="SWITCH_HANDLE",
                                track_id=7))
    ev = det_event(event_id=eid, stage="VERIFY", conf=0.9, p=120.0, zoom=2.4,
                   cls="PRESSURE_GAUGE", track_id=99)
    ev["detections"].append({"track_id": 100, "defect_class": "SWITCH_HANDLE",
                             "confidence": 0.88, "bbox": [0.0, 0.0, 118.0, 118.0],
                             "pixel_density_px": 118.0, "est_distance_m": 5.06,
                             "l2_reading": None})
    node.on_detection(ev)
    m = json.loads((node.packer.dir_for(node.run_id, eid) / "manifest.json")
                   .read_text(encoding="utf-8"))
    assert m["before"]["defect_class"] == m["after"]["defect_class"] == "SWITCH_HANDLE"


def test_mission_ctx_overrides_the_uploader_guess(node, tmp_path):
    """FSM 落在证据目录里的中止原因才是真的，覆盖 uploader 自己那条 TTL。

    评审看的就是这份 manifest，写错了等于把故障现场抹掉。
    """
    eid = "cccccccc-0000-4000-8000-000000000003"
    node.on_detection(det_event(event_id=eid, stage="CRUISE", conf=0.45,
                                p=49.9, zoom=1.0))
    d = node.packer.dir_for(node.run_id, eid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "mission_ctx.json").write_text(json.dumps({
        "abort": {"at_state": "AIM", "reason": "STATE_TIMEOUT",
                  "detail": "AIM 状态超时"},
        "timeline": [{"state": "SUSPECT", "duration_ms": 300},
                     {"state": "AIM", "duration_ms": 3000}],
        "waypoint_id": "WP-04"}, ensure_ascii=False), encoding="utf-8")
    node.pending[eid].abort = {"at_state": "VERIFY", "reason": "STATE_TIMEOUT",
                               "detail": "uploader 侧超时"}
    node._finish(node.pending[eid])              # noqa: SLF001
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert m["abort"]["at_state"] == "AIM"
    assert [t["state"] for t in m["timeline"]] == ["SUSPECT", "AIM"]
    assert m["waypoint_id"] == "WP-04"


def test_meta_jsonl_carries_both_interfaces(node):
    """meta.jsonl 要能逐帧重放，只有 IF-3 是重放不出来的——检出框在 IF-1 里。"""
    eid = "dddddddd-0000-4000-8000-000000000004"
    node.on_detection(det_event(event_id=eid, stage="CRUISE", conf=0.45,
                                p=49.9, zoom=1.0))
    node.on_status({"msg_type": "STATUS_REPORT", "report_kind": "PERIODIC",
                    "chassis": {"state": "STOPPED"}, "safety": None})
    node.on_detection(det_event(event_id=eid, stage="VERIFY", conf=0.9,
                                p=120.0, zoom=2.4, l2=reading()))
    lines = [json.loads(x) for x in
             (node.packer.dir_for(node.run_id, eid) / "meta.jsonl")
             .read_text(encoding="utf-8").splitlines()]
    kinds = {x["msg_type"] for x in lines}
    assert kinds == {"DETECTION_EVENT", "STATUS_REPORT"}


# ---------------------------------------------------------------- 分组统计
def test_gain_summary_must_be_grouped_by_verdict(node):
    """ICD §6.4 特意警告过：FALSE_ALARM 的 Δconf 是负的，混在一起算均值会
    接近零，看上去像复核完全没起作用。"""
    from patrol.tools.run_all import summarise
    pairs = [("11111111-0000-4000-8000-00000000000%d" % i, c_before, c_after, l2)
             for i, (c_before, c_after, l2) in enumerate(
                 [(0.45, 0.92, reading(value=1.45, in_band=False)),
                  (0.47, 0.90, reading(value=1.50, in_band=False)),
                  (0.41, 0.03, None), (0.44, 0.05, None)])]
    for eid, cb, ca, l2 in pairs:
        node.on_detection(det_event(event_id=eid, stage="CRUISE", conf=cb,
                                    p=49.9, zoom=1.0))
        node.on_detection(det_event(event_id=eid, stage="VERIFY", conf=ca,
                                    p=119.8, zoom=2.4, l2=l2))
    s = summarise(node.cfg)
    assert set(s["by_verdict"]) == {"READING_ABNORMAL", "FALSE_ALARM"}
    assert s["by_verdict"]["FALSE_ALARM"]["avg_delta_conf"] < 0
    assert s["by_verdict"]["READING_ABNORMAL"]["avg_delta_conf"] > 0.25
    # 混在一起算的话会被误报组拉到接近零——这正是必须分组的理由
    mixed = sum(v["avg_delta_conf"] * v["n"] for v in s["by_verdict"].values()) / s["total"]
    assert abs(mixed) < 0.15


def test_suppressed_events_do_not_become_evidence_packages(node):
    """被任务层抑制的可疑事件不出证据包。

    perception 一检出可疑目标就分配 event_id，但 mission 可能因为同目标冷却、
    同巡检位已测过、恢复静默或预算耗尽而根本不发起复核——那是抑制规则正常
    工作，不是复核失败。都打成 INCONCLUSIVE 的话，"复核成功率"的分母会被
    这些从未发起的事件撑大，指标就废了（实测把成功率从 100 % 拉到 60 %）。
    """
    eid = "eeeeeeee-0000-4000-8000-000000000005"
    node.stale_s = 0.0                                # 立刻过期
    node.on_detection(det_event(event_id=eid, stage="CRUISE", conf=0.45,
                                p=49.9, zoom=1.0))
    node._expire()                                    # noqa: SLF001
    assert eid not in node.pending
    assert not (node.packer.dir_for(node.run_id, eid) / "manifest.json").exists()


def test_aborted_verification_still_becomes_a_package(node):
    """反过来，状态机真的发起了复核又中止的，必须出包——失败样本最有调参价值。

    判据是 mission_ctx.json 在不在：状态机开过一次复核就会写下它。
    """
    eid = "ffffffff-0000-4000-8000-000000000006"
    node.stale_s = 0.0
    node.on_detection(det_event(event_id=eid, stage="CRUISE", conf=0.45,
                                p=49.9, zoom=1.0))
    d = node.packer.dir_for(node.run_id, eid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "mission_ctx.json").write_text(json.dumps({
        "abort": {"at_state": "AIM", "reason": "STATE_TIMEOUT", "detail": "AIM 超时"},
        "timeline": [{"state": "AIM", "duration_ms": 3000}]}, ensure_ascii=False),
        encoding="utf-8")
    node._expire()                                    # noqa: SLF001
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert m["verdict"]["result"] == "INCONCLUSIVE"
    assert m["abort"]["at_state"] == "AIM"
    assert m["gain"]["verify_success"] is False


def test_aborted_verification_is_packed_immediately_not_after_ttl(node):
    """状态机一中止就出包，不必等 60 s 的 TTL。

    只靠 TTL 兜底会漏：一轮巡检收工时最后几个中止事件还没到期就随进程没了，
    台账里查无此事——而中止的复核样本恰恰是最有调参价值的。
    """
    eid = "10d09cca-0000-4000-8000-000000000007"
    node.stale_s = 1e6                              # TTL 长到不可能触发
    node.on_detection(det_event(event_id=eid, stage="CRUISE", conf=0.45,
                                p=49.9, zoom=1.0))
    d = node.packer.dir_for(node.run_id, eid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "mission_ctx.json").write_text(json.dumps({
        "abort": {"at_state": "AIM", "reason": "STATE_TIMEOUT", "detail": "AIM 超时"}},
        ensure_ascii=False), encoding="utf-8")
    node._harvest_aborted()                         # noqa: SLF001
    assert eid not in node.pending
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert m["abort"]["at_state"] == "AIM"


def test_flush_on_shutdown_settles_open_verifications(node):
    """收工前把还开着的复核结清，但**只结清真的进入过复核的**。"""
    verified = "20000000-0000-4000-8000-000000000008"
    suppressed = "30000000-0000-4000-8000-000000000009"
    for eid in (verified, suppressed):
        node.on_detection(det_event(event_id=eid, stage="CRUISE", conf=0.45,
                                    p=49.9, zoom=1.0))
    d = node.packer.dir_for(node.run_id, verified)
    d.mkdir(parents=True, exist_ok=True)
    (d / "mission_ctx.json").write_text("{}", encoding="utf-8")
    node.flush()
    assert (d / "manifest.json").exists()
    assert not (node.packer.dir_for(node.run_id, suppressed) / "manifest.json").exists()
