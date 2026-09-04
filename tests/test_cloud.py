"""云端台账：入库、人工复核、模型版本、增益统计、文件收发。

`cloud/db.py` 与 `cloud/server.py` 此前覆盖率都是 **0**——而这是评审当天唯一
会被打开看的东西。三类东西在这里钉住：

- **增益统计必须按 verdict 分组**（ICD §6.4），混在一起算均值会接近零
- **不合规的 manifest 拒收**，台账里的每一条都必须是合规的
- **文件路径不许跑出 storage**：run_id / event_id / name 三段都来自 URL
"""
from __future__ import annotations

import json

import pytest

from patrol.common import messages as M

fastapi_testclient = pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient          # noqa: E402

from cloud.db import Ledger                        # noqa: E402
from cloud.server import create_app                # noqa: E402


def manifest(event_id, *, verdict="READING_OK", delta=0.46, ratio=2.4,
             ok=True, run_id="20260901-093012-a7f3", ts=1_700_000_000_000,
             waypoint="WP-07", abort=None, files=True):
    snap = {"confidence": 0.45, "pixel_density_px": 49.9, "zoom": 1.0,
            "est_distance_m": 5.06, "defect_class": "PRESSURE_GAUGE",
            "l2_reading": None}
    return M.build_evidence_package(
        run_id=run_id, event_id=event_id, waypoint_id=waypoint, ts_utc_ms=ts,
        verdict={"result": verdict, "defect_class": "PRESSURE_GAUGE",
                 "severity": "INFO", "needs_human_review": verdict == "INCONCLUSIVE",
                 "confidence": 0.91},
        before=snap,
        after=dict(snap, confidence=0.91, pixel_density_px=119.8, zoom=2.4),
        gain={"delta_conf": delta, "pixel_density_ratio": ratio,
              "verify_success": ok},
        timeline=[],
        files=[{"path": "cruise.jpg", "role": "CRUISE_ANNOTATED", "bytes": 12,
                "sha256": "0" * 64, "uploaded": False}] if files else [],
        abort=abort)


def eid(n: int) -> str:
    return "%08x-0000-4000-8000-000000000001" % n


@pytest.fixture()
def ledger(tmp_path):
    return Ledger(tmp_path / "t.db")


@pytest.fixture()
def client(tmp_path):
    from patrol.common.config import Config
    cfg = Config.load(overrides={
        "logging": {"dir": str(tmp_path / "logs")},
        "cloud": {"db": str(tmp_path / "c.db"), "storage": str(tmp_path / "store")}})
    return TestClient(create_app(cfg)), tmp_path / "store"


# ---------------------------------------------------------------- 台账
def test_insert_and_read_back(ledger):
    ledger.upsert_evidence(manifest(eid(1)), site_id="SITE-01", received_ms=1)
    d = ledger.get_evidence(eid(1))
    assert d["verdict"] == "READING_OK"
    assert d["manifest"]["gain"]["pixel_density_ratio"] == 2.4
    assert d["reviews"] == [] and d["assets"] == []


def test_reupload_updates_every_mutable_field(ledger):
    """补传一份更正过的 manifest，台账里不能还留着旧的巡检位。

    原来 ON CONFLICT 的更新列表漏了 waypoint_id / ts_utc_ms / site_id，
    重传之后这几项永远停在第一次入库的值上。
    """
    ledger.upsert_evidence(manifest(eid(2), waypoint="WP-07", ts=1000),
                           site_id="SITE-01", received_ms=1)
    ledger.upsert_evidence(manifest(eid(2), waypoint="WP-04", ts=2000,
                                    verdict="READING_ABNORMAL"),
                           site_id="SITE-02", received_ms=2)
    d = ledger.get_evidence(eid(2))
    assert d["waypoint_id"] == "WP-04"
    assert d["ts_utc_ms"] == 2000
    assert d["site_id"] == "SITE-02"
    assert d["verdict"] == "READING_ABNORMAL"
    assert len(ledger.list_evidence()) == 1, "重传不该产生第二条"


def test_missing_evidence_returns_none(ledger):
    assert ledger.get_evidence("nope") is None


def test_filters(ledger):
    ledger.upsert_evidence(manifest(eid(3), verdict="READING_OK"),
                           site_id="S", received_ms=1)
    ledger.upsert_evidence(manifest(eid(4), verdict="INCONCLUSIVE", ok=False),
                           site_id="S", received_ms=1)
    assert len(ledger.list_evidence(verdict="READING_OK")) == 1
    assert len(ledger.list_evidence(needs_review=True)) == 1
    assert len(ledger.list_evidence(run_id="不存在的")) == 0


def test_listing_is_newest_first(ledger):
    ledger.upsert_evidence(manifest(eid(5), ts=1000), site_id="S", received_ms=1)
    ledger.upsert_evidence(manifest(eid(6), ts=9000), site_id="S", received_ms=1)
    assert [r["event_id"] for r in ledger.list_evidence()] == [eid(6), eid(5)]


# ---------------------------------------------------------------- 复核
def test_review_is_recorded_separately_from_the_ai_verdict(ledger):
    """**裁决单独一张表，不覆盖 AI 的判定。**

    覆盖掉的话就没法统计"AI 判对了多少"——而误报率、漏报率正是要写进答辩
    材料的数。
    """
    ledger.upsert_evidence(manifest(eid(7), verdict="INCONCLUSIVE", ok=False),
                           site_id="S", received_ms=1)
    ledger.add_review(eid(7), "张三", "FALSE_ALARM", "玻璃反光", 5000)
    d = ledger.get_evidence(eid(7))
    assert d["verdict"] == "INCONCLUSIVE", "AI 的判定被人工裁决覆盖了"
    assert d["reviews"][0]["decision"] == "FALSE_ALARM"
    assert d["reviews"][0]["reviewer"] == "张三"
    assert d["needs_review"] == 0, "裁决之后应当移出待复核队列"


def test_multiple_reviews_are_kept_in_order(ledger):
    ledger.upsert_evidence(manifest(eid(8)), site_id="S", received_ms=1)
    ledger.add_review(eid(8), "甲", "NEED_MORE", "看不清", 1000)
    ledger.add_review(eid(8), "乙", "DEFECT", "确认是缺陷", 2000)
    assert [r["reviewer"] for r in ledger.reviews_of(eid(8))] == ["甲", "乙"]


# ---------------------------------------------------------------- 模型版本
def test_activating_a_model_deactivates_the_previous_one_in_the_same_stage(ledger):
    for v in ("yolo11s-v1", "yolo11s-v2"):
        ledger.register_model(v, "CRUISE", weights_sha=None, dataset="dr",
                              metrics={"mAP50": 0.87}, note="", ts_utc_ms=1,
                              activate=True)
    ledger.register_model("yolo11m-v1", "VERIFY", weights_sha=None, dataset="dr",
                          metrics={}, note="", ts_utc_ms=1, activate=True)
    active = {m["stage"]: m["version"] for m in ledger.models() if m["active"]}
    assert active == {"CRUISE": "yolo11s-v2", "VERIFY": "yolo11m-v1"}, \
        "同一 stage 下不该有两个 active，不同 stage 之间不该互相顶掉"


# ---------------------------------------------------------------- 增益统计
def test_gain_stats_are_grouped_by_verdict(ledger):
    """**ICD §6.4 特意警告过的地方。**

    FALSE_ALARM 的 delta_conf 是负值（复核把一个 0.41 的误检压到 0.05），
    与真缺陷混在一起算均值会接近零，看上去像是复核完全没起作用。
    """
    for i, (v, d, r, ok) in enumerate([
            ("READING_ABNORMAL", 0.46, 2.3, True),
            ("READING_ABNORMAL", 0.42, 2.2, True),
            ("FALSE_ALARM", -0.38, 2.1, True),
            ("FALSE_ALARM", -0.44, 2.0, True),
            ("INCONCLUSIVE", -0.45, 0.0, False)]):
        ledger.upsert_evidence(manifest(eid(100 + i), verdict=v, delta=d,
                                        ratio=r, ok=ok), site_id="S", received_ms=1)
    st = ledger.gain_stats()
    assert set(st["by_verdict"]) == {"READING_ABNORMAL", "FALSE_ALARM", "INCONCLUSIVE"}
    assert st["by_verdict"]["FALSE_ALARM"]["avg_delta_conf"] < 0
    assert st["delta_conf_on_real_defects"] == pytest.approx(0.44, abs=0.01)
    assert st["verify_success_rate"] == pytest.approx(4 / 5)
    # 混着算会被误报组拉到接近零——这正是必须分组的理由
    mixed = sum(g["avg_delta_conf"] * g["n"] for g in st["by_verdict"].values()) / st["total"]
    assert abs(mixed) < 0.12


def test_gain_stats_on_empty_ledger_does_not_divide_by_zero(ledger):
    st = ledger.gain_stats()
    assert st["total"] == 0 and st["verify_success_rate"] == 0.0
    assert st["delta_conf_on_real_defects"] is None


def test_gain_stats_can_be_scoped_to_one_run(ledger):
    ledger.upsert_evidence(manifest(eid(9), run_id="20260901-093012-aaaa"),
                           site_id="S", received_ms=1)
    ledger.upsert_evidence(manifest(eid(10), run_id="20260901-093012-bbbb"),
                           site_id="S", received_ms=1)
    assert ledger.gain_stats("20260901-093012-aaaa")["total"] == 1
    assert len(ledger.runs()) == 2


# ---------------------------------------------------------------- HTTP
def test_post_evidence_rejects_a_non_compliant_manifest(client):
    c, _ = client
    bad = manifest(eid(11))
    bad["verdict"]["result"] = "随便编的结论"
    r = c.post("/api/evidence", json={"site_id": "S", "manifest": bad})
    assert r.status_code == 422, "不合规的 manifest 应当拒收，台账里每条都必须合规"
    assert c.get("/api/evidence").json() == []


def test_full_http_round_trip(client):
    c, store = client
    r = c.post("/api/evidence", json={"site_id": "S", "manifest": manifest(eid(12))})
    assert r.status_code == 200 and r.json()["files_expected"] == 1

    import hashlib
    data = b"fake-jpeg-bytes"
    sha = hashlib.sha256(data).hexdigest()
    r = c.put("/api/evidence/20260901-093012-a7f3/%s/files/cruise.jpg" % eid(12),
              content=data, headers={"X-Sha256": sha})
    assert r.status_code == 200 and r.json()["sha256"] == sha

    r = c.get("/api/files/20260901-093012-a7f3/%s/cruise.jpg" % eid(12))
    assert r.status_code == 200 and r.content == data

    d = c.get("/api/evidence/%s" % eid(12)).json()
    assert d["assets"][0]["name"] == "cruise.jpg"


def test_hash_mismatch_is_rejected_so_the_edge_retransmits(client):
    c, _ = client
    c.post("/api/evidence", json={"manifest": manifest(eid(13))})
    r = c.put("/api/evidence/r/%s/files/x.jpg" % eid(13),
              content=b"abc", headers={"X-Sha256": "f" * 64})
    assert r.status_code == 409


def test_review_endpoint_validates_the_decision(client):
    c, _ = client
    c.post("/api/evidence", json={"manifest": manifest(eid(14))})
    assert c.post("/api/evidence/%s/review" % eid(14),
                  json={"decision": "乱写", "reviewer": "甲"}).status_code == 400
    assert c.post("/api/evidence/%s/review" % eid(14),
                  json={"decision": "false_alarm", "reviewer": "甲"}).status_code == 200
    assert c.post("/api/evidence/不存在/review",
                  json={"decision": "CONFIRM"}).status_code == 404


def test_healthz_and_index(client):
    c, _ = client
    assert c.get("/healthz").json()["ok"] is True
    assert c.get("/").status_code == 200


# ---------------------------------------------------------------- 路径安全
@pytest.mark.parametrize("run_id,event_id,name", [
    ("..", "..", "escaped.txt"),
    ("ok", "..", "escaped.txt"),
    ("ok", "ok", ".."),
    (".", "ok", "x.txt"),
    ("%2e%2e", "ok", "x.txt"),
])
def test_upload_cannot_escape_the_storage_directory(client, run_id, event_id, name):
    """**run_id / event_id / name 三段都来自 URL，三段都要校验。**

    原来只校验了 name，`PUT /api/evidence/../../x/files/y` 就能往 storage
    之外写文件。逐段过滤黑名单也不够稳，所以改成 resolve 之后比较前缀。
    """
    c, store = client
    r = c.put("/api/evidence/%s/%s/files/%s" % (run_id, event_id, name),
              content=b"payload")
    # 400 = 被 safe_path 拦下；404 = 路由层就把 `..` 规范化掉了、压根没匹配上。
    # 两条都算安全，**不安全的只有 2xx**。两道都要有：路由层的规范化行为随
    # ASGI 服务器实现而变，而 %2e%2e 这类编码变体确实会一路走到处理函数里。
    assert r.status_code in (400, 404), "越界的路径被放行了：%d" % r.status_code
    escaped = list(store.parent.glob("escaped.txt")) + \
        list(store.parent.glob("*/escaped.txt"))
    assert not escaped, "有文件被写到 storage 之外：%s" % escaped


@pytest.mark.parametrize("run_id,event_id,name", [
    ("..", "..", "passwd"), ("ok", "..", "x"), ("ok", "ok", ".."),
])
def test_download_cannot_escape_the_storage_directory(client, run_id, event_id, name):
    c, _ = client
    r = c.get("/api/files/%s/%s/%s" % (run_id, event_id, name))
    assert r.status_code in (400, 404), "越界的读取被放行了"


def test_percent_encoded_traversal_reaches_the_handler_and_is_blocked(client):
    """**这条才是真正会走到处理函数里的那种。**

    `..` 作为路径片段多半在路由层就被规范化掉了（返回 404），但
    `%2e%2e` 会一路解码后送进处理函数——只靠"路由层会帮我挡"是不成立的。
    """
    c, store = client
    for run_id, event_id, name in (("%2e%2e", "%2e%2e", "escaped.txt"),
                                   ("ok", "%2e%2e", "escaped.txt"),
                                   ("ok", "ok", "%2e%2e")):
        r = c.put("/api/evidence/%s/%s/files/%s" % (run_id, event_id, name),
                  content=b"payload")
        assert r.status_code == 400, \
            "%s/%s/%s 被放行了：%d" % (run_id, event_id, name, r.status_code)
    assert not list(store.parent.glob("escaped.txt"))


def test_oversized_upload_is_rejected(tmp_path):
    from patrol.common.config import Config
    cfg = Config.load(overrides={
        "logging": {"dir": str(tmp_path / "logs")},
        "cloud": {"db": str(tmp_path / "c.db"), "storage": str(tmp_path / "s"),
                  "max_file_bytes": 64}})
    c = TestClient(create_app(cfg))
    c.post("/api/evidence", json={"manifest": manifest(eid(15))})
    r = c.put("/api/evidence/r/%s/files/big.bin" % eid(15), content=b"x" * 200)
    assert r.status_code == 413


def test_list_limit_is_clamped(client):
    c, _ = client
    assert c.get("/api/evidence?limit=999999999").status_code == 200
    assert c.get("/api/evidence?limit=0").status_code == 200


# ================================================================== 实时遥测
#
# 这一路和台账的性质完全不同：不落库、只留最近一段、断了重连从当前状态接着看。
# 用例钉的正是这个分界——**实时数据不许污染台账库**。
class TestLive:
    def test_push_then_read_back(self, client):
        client, _storage = client
        r = client.post("/api/live/push", json={
            "commands": [{"ts_utc_ms": 10, "target": "云台", "text": "转到 pan=+90.0°",
                          "ok": True, "latency_ms": 1.2, "command": "PTZ_SET"}],
            "snapshot": {"x_m": 12.4, "y_m": -3.18, "pan_deg": 90.0, "zoom": 2.4}})
        assert r.status_code == 200 and r.json()["buffered"] == 1
        s = client.get("/api/live/state").json()
        assert s["commands"][0]["text"].startswith("转到")
        assert s["snapshot"]["zoom"] == 2.4

    def test_after_ms_only_returns_unseen_commands(self):
        """网页每 500 ms 轮询一次，重复发同一批会让流水刷屏。"""
        from cloud.server import create_app
        from fastapi.testclient import TestClient
        c = TestClient(create_app())
        c.post("/api/live/push", json={"commands": [
            {"ts_utc_ms": 10, "text": "a"}, {"ts_utc_ms": 20, "text": "b"}]})
        assert [x["text"] for x in c.get("/api/live/state?after_ms=10").json()["commands"]] == ["b"]
        assert c.get("/api/live/state?after_ms=99").json()["commands"] == []

    def test_live_data_never_reaches_the_ledger(self, client):
        """**实时流不落库。**它每秒几十条、没有留存价值，写进去只会撑爆台账。"""
        client, _storage = client
        before = len(client.get("/api/evidence").json())
        client.post("/api/live/push", json={"commands": [
            {"ts_utc_ms": 1, "text": "x"} for _ in range(50)]})
        assert len(client.get("/api/evidence").json()) == before

    def test_ring_buffer_bounds_memory(self, client):
        """车跑一天不能把云端内存吃光。"""
        client, _storage = client
        for i in range(600):
            client.post("/api/live/push",
                        json={"commands": [{"ts_utc_ms": i, "text": "x"}]})
        assert len(client.get("/api/live/state?limit=400").json()["commands"]) <= 400

    def test_stale_seconds_tells_the_page_the_car_went_quiet(self, client):
        """静默停更新最难查。如实报"已 N 秒没有数据"比页面看起来正常好。"""
        client, _storage = client
        assert client.get("/api/live/state").json()["stale_s"] is None
        client.post("/api/live/push", json={"commands": []})
        assert client.get("/api/live/state").json()["stale_s"] is not None

    def test_malformed_push_is_rejected_not_crashed(self, client):
        client, _storage = client
        assert client.post("/api/live/push",
                           json={"commands": "不是数组"}).status_code == 400
        client.post("/api/live/push", json={"commands": [1, 2, "x"]})   # 非法元素跳过
        assert client.get("/api/live/state").json()["commands"] == []

    def test_map_gives_the_page_its_static_backdrop(self, client):
        """俯视图的底图一轮里一次都不动，分开取一次，别每次轮询都捎上。"""
        client, _storage = client
        m = client.get("/api/live/map").json()
        assert m["waypoints"] and m["targets"]
        assert all({"id", "x_m", "y_m"} <= set(w) for w in m["waypoints"])


# ---------------------------------------------------------------- 模型版本登记
def test_register_models_reads_deliverables(tmp_path):
    """任务书第 7 项：模型版本管理。接口一直在，登记数一直是 0。

    这条用例盯的是"登记脚本能从三人交付里读出元数据并写进台账"，不是盯具体数字
    ——数字属于交付方，改了不该让这条红。
    """
    from patrol.tools import register_models as RM

    items = RM.collect()
    assert items, "deliverables/ 下应当能收集到模型元数据"
    stages = {it["stage"] for it in items}
    assert {"cruise", "segment", "anomaly"} <= stages, stages

    # 权重不在库里的一律留空，不许编哈希
    for it in items:
        assert it["weights_sha"] is None or len(it["weights_sha"]) == 64, it["version"]

    # 只能有一条 active，且必须是系统当前真正启用的那一路（零权重统计法）
    active = [it for it in items if it["activate"]]
    assert len(active) == 1 and active[0]["stage"] == "anomaly", active

    db = tmp_path / "ledger.db"
    assert RM.main(["--db", str(db)]) == 0
    from cloud.db import Ledger
    got = Ledger(str(db)).models()
    assert len(got) == len(items)
