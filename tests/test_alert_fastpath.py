"""「发现即报」快通路：suspect 一确认就报，不等复核走完。

**为什么要这条旁路。**证据包要等整个复核周期走完才存在——FSM 预算加总
9.2 s，最坏超时 22 s。只有证据包一条路的话，"一发现环境变化或未识别的问题
就立即上报"这条要求实际变成"发现后十几秒才上报"。实测（跑一轮 150 s、6 组
配对）告警比证据包**中位提前 17.4 s**，最小 12.2 s。

**两条通路的保证级别是故意不同的，这些测试就是钉住这个区别：**

- 告警：秒级、轻量、不带图、**允许丢**。断网发不出去就算了，不落盘不重传。
- 证据包：**保证不丢**，走 UploadQueue 断点续传，未确认的数据永不自动删除。

丢一条告警不丢信息——同一次复核的证据包随后会带着完整数据到达。告警买的是
**时延**，不是可靠性。所以最要紧的一条不变量是：**告警这条旁路无论怎么坏，
都不能影响证据包那条主路。**下面 test_*_never_breaks_the_evidence_path
就是它，写这个文件的直接原因是它真的坏过一次（假 transport 没有 send_alert，
旁路抛 AttributeError，把整个 on_detection 掀翻，11 条 uploader 测试全红）。
"""
from __future__ import annotations

import pytest

from patrol.common.config import Config
from patrol.uploader.node import UploaderNode


class _Bus:
    def drain(self, max_n=0):
        return []

    def close(self):
        pass


def _mk_transport(*, alerts: list | None = None, raises=False, omit=False):
    """造一个假 transport。omit=True 时**故意不实现 send_alert**。"""
    body = {"send_manifest": staticmethod(lambda m: True),
            "put_file": staticmethod(lambda *a, **k: True),
            "close": staticmethod(lambda: None)}
    if not omit:
        def _send(a):
            if raises:
                raise RuntimeError("网络炸了")
            if alerts is not None:
                alerts.append(a)
            return True
        body["send_alert"] = staticmethod(_send)
    return type("T", (), body)()


@pytest.fixture()
def make_node(tmp_path, monkeypatch):
    monkeypatch.setattr("patrol.uploader.node.Subscriber", lambda *a, **k: _Bus())
    created = []

    def _make(transport):
        cfg = Config.load(overrides={
            "logging": {"dir": str(tmp_path / "logs")},
            "uploader": {"evidence_dir": str(tmp_path / "evidence"),
                         "upload_period_s": 1e6}})
        n = UploaderNode(cfg)
        n.queue.transport = transport
        created.append(n)
        return n

    yield _make
    for n in created:
        n.close()


def _ev(*, event_id="E1", stage="CRUISE", track_id=7, suspect=True,
        cls="PRESSURE_GAUGE", p=25.0, conf=0.42):
    return {
        "msg_type": "DETECTION_EVENT", "run_id": "20260901-093012-a7f3",
        "event_id": event_id, "stage": stage, "ts_utc_ms": 1700000000000,
        "context": {"waypoint_id": "WP-07",
                    "pose": {"x_m": 12.43, "y_m": -3.18, "yaw_deg": 0.0},
                    "ptz": {"pan_deg": 90.0, "tilt_deg": 2.0, "zoom": 1.0,
                            "hfov_deg": 60.0}},
        "detections": [{"track_id": track_id, "defect_class": cls,
                        "confidence": conf, "bbox": [0.0, 0.0, p, p],
                        "pixel_density_px": p, "est_distance_m": 9.89,
                        "l2_reading": None}],
        "suspect": ({"is_suspect": True, "target_track_id": track_id,
                     "trigger_rule": "CONF_BAND", "severity": "WARN"}
                    if suspect else None),
    }


# ---------------------------------------------------------------- 触发
def test_alert_fires_on_first_suspect(make_node):
    """suspect 一出现就发，带着够人做决定的字段。"""
    got: list = []
    n = make_node(_mk_transport(alerts=got))
    n.on_detection(_ev())
    assert len(got) == 1
    a = got[0]
    assert a["event_id"] == "E1"
    assert a["defect_class"] == "PRESSURE_GAUGE"
    assert a["trigger_rule"] == "CONF_BAND"
    assert a["pixel_density_px"] == pytest.approx(25.0)
    assert a["waypoint_id"] == "WP-07"
    assert (a["x_m"], a["y_m"]) == (12.43, -3.18)
    # 不带图：一张巡航帧 200 KB 上下，断网恢复时先该挤出去的是"哪里有情况"
    assert not any(k for k in a if "file" in k or "image" in k or "jpg" in str(a[k]))


def test_alert_is_sent_only_once_per_event(make_node):
    """IF-1 是 10 Hz 的，同一个 suspect 会连着出现几十帧。

    不去重的话云端被同一件事刷屏，真正的新情况反而被淹掉。
    """
    got: list = []
    n = make_node(_mk_transport(alerts=got))
    for _ in range(30):
        n.on_detection(_ev())
    assert len(got) == 1


def test_distinct_events_each_get_their_own_alert(make_node):
    """去重是按 event_id 的，不是全局只发一条。"""
    got: list = []
    n = make_node(_mk_transport(alerts=got))
    n.on_detection(_ev(event_id="E1"))
    n.on_detection(_ev(event_id="E2", track_id=9))
    assert [a["event_id"] for a in got] == ["E1", "E2"]


def test_no_suspect_no_alert(make_node):
    """没有 suspect 就是普通巡航帧，不该报。"""
    got: list = []
    n = make_node(_mk_transport(alerts=got))
    n.on_detection(_ev(suspect=False))
    assert got == []


# ---------------------------------------------------------------- 隔离
@pytest.mark.parametrize("kind", ["raises", "omit"])
def test_broken_alert_path_never_breaks_the_evidence_path(make_node, kind):
    """**这是本文件最要紧的一条。**旁路怎么坏都不能掀翻主路。

    两种坏法都要覆盖：
    - raises：transport 实现了 send_alert 但抛异常（真实场景：网络炸了）
    - omit ：transport 根本没有 send_alert（真实场景：测试替身、自定义后端）

    第二种真的发生过——假 transport 没这个方法，旁路抛 AttributeError，
    把 on_detection 整个掀翻，11 条 uploader 测试一起红。
    """
    n = make_node(_mk_transport(raises=(kind == "raises"), omit=(kind == "omit")))
    n.on_detection(_ev())                       # 不抛即通过

    # 而且主路的状态必须照常推进：before 快照要记下来，否则证据包算不出增益
    p = n.pending["E1"]
    assert p.before is not None, "旁路出问题时 before 快照丢了，证据包会算不出 Δconf"
    assert p.defect_class == "PRESSURE_GAUGE"


def test_alert_failure_does_not_retry(make_node):
    """发失败不重试——告警是尽力而为，重传是证据包的事。

    如果这里加了重试，断网时 uploader 会卡在重试上，不再排空 IF-1/IF-3、
    也不再打包新证据——而断网正是最需要它继续在本地打包的时候。
    """
    calls = []

    def _send(a):
        calls.append(a)
        return False                            # 一直失败

    t = _mk_transport()
    t.__class__.send_alert = staticmethod(_send)
    n = make_node(t)
    for _ in range(10):
        n.on_detection(_ev())
    assert len(calls) == 1, "失败后不该重发"
