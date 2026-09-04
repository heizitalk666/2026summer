"""安全网关：三层机制必须可当场演示，不能只是声明。

方案书 §7.4「可验证性」：安全设计的说服力不在于声明，而在于可以当场演示
三件事——构造越界指令时网关拒绝并留下审计记录；强制终止识别进程后车辆
自行恢复巡航；注入安全事件时正在进行的测量在 200 ms 内中止。
本文件覆盖第一、二件，第三件在 test_fsm.py。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from patrol import SCHEMA_VERSION
from patrol.common import messages as M
from patrol.common.bus import Requester
from patrol.common.clock import stamps
from patrol.common.config import Config
from patrol.common.ids import new_run_id, new_uuid
from patrol.gateway import limits as L
from patrol.gateway.node import GatewayNode

RUN_ID = new_run_id()


def mk(command, params, *, issued_by="MISSION_FSM", timeout_ms=2000, **kw):
    mono, utc = stamps()
    d = {"schema_version": SCHEMA_VERSION, "msg_type": "CONTROL_COMMAND",
         "cmd_id": new_uuid(), "seq": 1, "ts_mono_ns": mono, "ts_utc_ms": utc,
         "run_id": RUN_ID, "event_id": new_uuid(), "issued_by": issued_by,
         "command": command, "params": params, "timeout_ms": timeout_ms}
    d.update(kw)
    return d


@pytest.fixture()
def gw(cfg_ports, tmp_path):
    cfg = Config.load(overrides={
        "bus": cfg_ports.get("bus"),
        "gateway": {"audit_log": str(tmp_path / "audit.jsonl")},
        "logging": {"dir": str(tmp_path / "logs"), "level": "ERROR"},
    })
    node = GatewayNode(cfg, seed=11)
    yield node
    node.close()


# ---------------------------------------------------------------- 白名单
def test_whitelist_has_no_low_level_control():
    """协议层：识别模块在协议上无法表达底层控制意图。"""
    assert L.WHITELIST == {"PAUSE", "RESUME", "CREEP_FORWARD",
                           "GOTO_OBSERVE", "PTZ_SET", "HEARTBEAT"}
    for forbidden in ("SET_SPEED", "SET_STEER", "SET_TORQUE", "BRAKE"):
        assert forbidden not in L.WHITELIST_WITH_RATE


def test_forbidden_params_rejected_even_on_legal_command(gw):
    """command 合法但 params 夹带底层控制量，一样拒绝。"""
    ack = gw.handle_command(mk("PAUSE", {"reason": "VERIFY_REQUEST", "steer_deg": 12.0}))
    assert ack["result"] == "REJECTED"
    assert ack["checks"]["whitelist"] == "PASS"
    assert ack["checks"]["schema"] == "FAIL"


# ---------------------------------------------------------------- 越界
@pytest.mark.parametrize("cmd,params", [
    ("CREEP_FORWARD", {"distance_m": 1.20}),
    ("CREEP_FORWARD", {"distance_m": 0.01}),
    ("GOTO_OBSERVE", {"waypoint_id": "WP-07", "tolerance_m": 0.90}),
    ("PTZ_SET", {"pan_deg": 200.0, "tilt_deg": 0.0, "zoom": 1.0, "speed": "NORMAL"}),
    ("PTZ_SET", {"pan_deg": 0.0, "tilt_deg": -60.0, "zoom": 1.0, "speed": "NORMAL"}),
    ("PTZ_SET", {"pan_deg": 0.0, "tilt_deg": 0.0, "zoom": 5.0, "speed": "NORMAL"}),
    ("PTZ_RATE", {"pan_dps": 180.0, "tilt_dps": 0.0, "ttl_ms": 300}),
    ("PTZ_RATE", {"pan_dps": 10.0, "tilt_dps": 0.0, "ttl_ms": 5000}),
])
def test_out_of_range_is_rejected_not_truncated(gw, cmd, params):
    """越界一律拒绝，**不做截断**。

    截断会让 AI 侧的 bug 静默通过：发了 5 m 的 CREEP_FORWARD 被截成 0.5 m
    照常执行，联调时看不出问题，等到某次截断逻辑失效就出事。
    """
    ack = gw.handle_command(mk(cmd, params))
    assert ack["result"] == "REJECTED"
    assert ack["reject_code"] == "PARAM_OUT_OF_RANGE"
    assert ack["exec_handle"] is None
    # ICD §4.6 的拒绝示例：schema 通过、range 失败
    assert ack["checks"]["schema"] == "PASS"
    assert ack["checks"]["range"] == "FAIL"


def test_reject_detail_matches_icd_format(gw):
    ack = gw.handle_command(mk("CREEP_FORWARD", {"distance_m": 1.20}))
    assert ack["reject_detail"] == "CREEP_FORWARD.distance_m=1.200 exceeds [0.05,0.50]"


def test_unknown_waypoint(gw):
    ack = gw.handle_command(mk("GOTO_OBSERVE", {"waypoint_id": "WP-99", "tolerance_m": 0.2}))
    assert ack["reject_code"] == "UNKNOWN_WAYPOINT"


def test_100_out_of_range_commands_all_blocked(gw):
    """方案书 §9.3 验收项：构造超范围指令 100 次，拦截率 100 %，有审计记录。"""
    import numpy as np
    rng = np.random.default_rng(5)
    blocked = 0
    for i in range(100):
        kind = i % 4
        if kind == 0:
            c = mk("CREEP_FORWARD", {"distance_m": float(rng.uniform(0.51, 8.0))})
        elif kind == 1:
            c = mk("PTZ_SET", {"pan_deg": float(rng.uniform(171, 400)), "tilt_deg": 0.0,
                               "zoom": 1.0, "speed": "NORMAL"})
        elif kind == 2:
            c = mk("PTZ_SET", {"pan_deg": 0.0, "tilt_deg": 0.0,
                               "zoom": float(rng.uniform(3.01, 20.0)), "speed": "NORMAL"})
        else:
            c = mk("GOTO_OBSERVE", {"waypoint_id": "WP-07",
                                    "tolerance_m": float(rng.uniform(0.51, 5.0))})
        ack = gw.handle_command(c)
        if ack["result"] == "REJECTED" and ack["reject_code"] == "PARAM_OUT_OF_RANGE":
            blocked += 1
    assert blocked == 100, "拦截率必须是 100 %"

    # 审计记录必须留下
    lines = Path(gw.audit.path).read_text(encoding="utf-8").strip().splitlines()
    recs = [json.loads(x) for x in lines]
    rejected = [r for r in recs if r["result"] == "REJECTED"]
    assert len(rejected) >= 100
    assert all(r["checks"]["range"] == "FAIL" for r in rejected[-100:])


def test_checks_are_observable(gw):
    """checks 字段让"网关到底有没有在校验"可观测。

    评审时抽查日志，如果某一项长期是 SKIP，说明那层校验根本没接上。
    """
    ack = gw.handle_command(mk("PTZ_SET", {"pan_deg": 0.0, "tilt_deg": 0.0,
                                           "zoom": 1.0, "speed": "NORMAL"}))
    assert ack["result"] == "ACCEPTED"
    assert all(v == "PASS" for v in ack["checks"].values()), \
        "合法指令必须五项全 PASS，没有一项是 SKIP"


def test_ack_passes_schema(gw):
    for c in (mk("PTZ_SET", {"pan_deg": 0.0, "tilt_deg": 0.0, "zoom": 1.0, "speed": "NORMAL"}),
              mk("CREEP_FORWARD", {"distance_m": 9.9})):
        M.validate(gw.handle_command(c), "COMMAND_ACK")


# ---------------------------------------------------------------- 看门狗
def test_watchdog_not_armed_before_first_heartbeat(gw, monkeypatch):
    """开机时 mission 还没连上来，此时不该倒计时。

    看门狗监视的是"曾经在、现在断了"，不是"从来没来过"。不这样做的话
    车还没跑就先被判定 AI 失联，实测启动瞬间必然误触发。
    """
    from patrol.common import clock
    assert not gw.watchdog.armed
    base = clock.mono_ns()
    monkeypatch.setattr("patrol.gateway.watchdog.mono_ns", lambda: base + 9_000_000_000)
    assert gw.watchdog.check() is False, "还没收到过心跳就不该触发"
    assert gw.watchdog.heartbeat_ok


def test_heartbeat_is_exempt_from_heartbeat_lost(gw, monkeypatch):
    """**看门狗态下心跳必须放行，否则永远恢复不了。**

    ICD §4.5 里两句话自相矛盾：「心跳超时期间拒绝一切 issued_by =
    MISSION_FSM 的指令」与「恢复条件：心跳恢复且连续 3 条正常」。心跳本身
    就是 MISSION_FSM 发的，按前一句拒掉之后后一句永远不可能满足——看门狗
    一旦触发就死锁，AI 进程重启回来也接管不了车。实测确实如此。
    """
    from patrol.common import clock
    gw.handle_command(mk("HEARTBEAT", {"mission_state": "CRUISE"}))
    base = clock.mono_ns()
    monkeypatch.setattr("patrol.gateway.watchdog.mono_ns", lambda: base + 2_000_000_000)
    assert gw.watchdog.check() is True
    monkeypatch.undo()

    # 动作指令被拒
    assert gw.handle_command(mk("PTZ_SET", {"pan_deg": 0.0, "tilt_deg": 0.0,
                                            "zoom": 1.0, "speed": "NORMAL"})
                             )["reject_code"] == "HEARTBEAT_LOST"
    # 心跳放行，且连续三条能解除看门狗
    for _ in range(L.HEARTBEAT_RECOVER_COUNT):
        assert gw.handle_command(mk("HEARTBEAT", {"mission_state": "CRUISE"})
                                 )["result"] == "ACCEPTED"
    assert not gw.watchdog.triggered
    assert gw.handle_command(mk("PTZ_SET", {"pan_deg": 0.0, "tilt_deg": 0.0,
                                            "zoom": 1.0, "speed": "NORMAL"})
                             )["result"] == "ACCEPTED"


def test_watchdog_timeout_issues_resume(gw, monkeypatch):
    """心跳丢失 → 网关自行下发 RESUME，让车**走完路线**而不是停住。"""
    from patrol.common import clock
    gw.handle_command(mk("HEARTBEAT", {"mission_state": "CRUISE"}))
    assert not gw.watchdog.triggered

    # 把单调钟往前推 2 秒，模拟 mission 进程死了
    base = clock.mono_ns()
    monkeypatch.setattr("patrol.gateway.watchdog.mono_ns",
                        lambda: base + 2_000_000_000)
    assert gw.watchdog.check() is True
    assert gw.watchdog.check() is False, "只触发一次"

    ack = gw.handle_command(mk("PTZ_SET", {"pan_deg": 0.0, "tilt_deg": 0.0,
                                           "zoom": 1.0, "speed": "NORMAL"}))
    assert ack["reject_code"] == "HEARTBEAT_LOST", "看门狗态下拒绝一切 MISSION_FSM 指令"

    # 看门狗自己下发的 RESUME 不受此限
    ack2 = gw.handle_command(mk("RESUME", {}, issued_by="WATCHDOG"))
    assert ack2["result"] == "ACCEPTED"


def test_watchdog_recovers_after_three_heartbeats(gw, monkeypatch):
    from patrol.common import clock
    gw.handle_command(mk("HEARTBEAT", {"mission_state": "CRUISE"}))   # 先武装
    base = clock.mono_ns()
    monkeypatch.setattr("patrol.gateway.watchdog.mono_ns", lambda: base + 2_000_000_000)
    assert gw.watchdog.check() is True
    monkeypatch.undo()
    for i in range(L.HEARTBEAT_RECOVER_COUNT):
        released = gw.watchdog.on_heartbeat()
        assert released == (i == L.HEARTBEAT_RECOVER_COUNT - 1)
    assert not gw.watchdog.triggered


def test_heartbeat_constants_match_icd():
    """C2：取 ICD 的 5 Hz 而非方案书的 2 Hz。1500 ms 超时下 5 Hz 要连丢 7 条。"""
    assert L.HEARTBEAT_PERIOD_MS == 200
    assert L.HEARTBEAT_TIMEOUT_MS == 1500
    assert L.WATCHDOG_ACTION == "RESUME"
    assert L.HEARTBEAT_TIMEOUT_MS / L.HEARTBEAT_PERIOD_MS >= 7


# ---------------------------------------------------------------- 安全事件
def _wait_until(cond, timeout_s: float = 5.0, poll_s: float = 0.005) -> bool:
    """轮询到条件成立。**用来取代 time.sleep(固定值)。**

    原来这几处写的是 `time.sleep(0.05)`，赌的是"安全事件 50 ms 内一定传到
    网关看得见的地方"。这个赌注在空闲机器上成立，机器一忙就不成立：底盘桩
    的状态由**后台线程**推进，线程被推迟，`status().safety_layer_active`
    就还没翻上来，于是 RESUME 被放行、断言挂掉。实测满负载下 3/3 必挂。

    轮询版在空闲时反而更快（条件一成立立刻返回，通常几毫秒就够），只在机器
    忙的时候才多等；而且它等到的是**事实**，不是一个猜出来的时长。
    """
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        try:
            if cond():
                return True
        except Exception:                       # noqa: BLE001
            pass                                # 状态还没就绪，下一轮再看
        time.sleep(poll_s)
    return False


def test_safety_event_blocks_motion_commands(gw):
    """底盘安全层介入期间，网关拒绝一切运动指令，但云台指令放行。

    云台放行是有意的：复核中止时恰恰需要把云台归位。
    """
    gw.chassis.force_safety_event("OBSTACLE_DETECTED")
    assert _wait_until(lambda: gw.chassis.status().safety_layer_active), \
        "安全事件没能在超时内反映到底盘状态里，后面的断言无从谈起"
    assert gw.handle_command(mk("RESUME", {}))["reject_code"] == "SAFETY_OVERRIDE"
    ack = gw.handle_command(mk("PTZ_SET", {"pan_deg": 0.0, "tilt_deg": 0.0,
                                           "zoom": 1.0, "speed": "NORMAL"}))
    assert ack["result"] == "ACCEPTED", "云台动作不改变车辆运动，安全事件期间应放行"


def test_estop_requires_manual_clear(gw):
    """急停是唯一不能自恢复的安全事件。"""
    gw.chassis.force_safety_event("ESTOP_PRESSED")
    assert _wait_until(lambda: gw.chassis.status().safety_layer_active), \
        "急停没能在超时内反映到底盘状态里"
    ack = gw.handle_command(mk("RESUME", {}, issued_by="WATCHDOG"))
    assert ack["reject_code"] in ("ESTOP_ACTIVE", "SAFETY_OVERRIDE")
    gw.chassis.clear_estop()
    # 清完再等一次：这个 fixture 之后还要被 close，留着未清的急停会让下一条
    # 用例从一个非预期的状态开始（同一族的耦合 bug 就是这么来的）
    _wait_until(lambda: not gw.chassis.status().safety_layer_active)


# ---------------------------------------------------------------- 端到端
def test_end_to_end_over_zeromq(gw):
    """真的走一遍 REQ/REP，证明传输层通。"""
    t = threading.Thread(target=gw.serve_forever, daemon=True)
    t.start()
    time.sleep(0.2)
    req = Requester(gw.cfg.get("bus.command"), timeout_ms=2000)
    try:
        ack = req.request(mk("HEARTBEAT", {"mission_state": "CRUISE"}))
        assert ack["result"] == "ACCEPTED"
        ack = req.request(mk("CREEP_FORWARD", {"distance_m": 1.20}))
        assert ack["reject_code"] == "PARAM_OUT_OF_RANGE"
        M.validate(ack, "COMMAND_ACK")
    finally:
        req.close()
        gw.stop()
        t.join(timeout=2.0)


# ---------------------------------------------------------------- D3 决议 D1
def test_brake_latency_over_limit_is_reported_not_dropped():
    """制动超标的报文必须**能通过 Schema**，否则最该留证的证据被丢掉。

    D3 决议 D1：Schema 原来把 `brake_latency_ms` 的上限焊在验收指标 100 ms 上，
    于是真机制动慢到 150 ms 时，系统拿到的不是"一条记录着超标的报文"，而是一条
    解析失败的报文。上限已放宽到 5000 ms，100 ms 的判定改由网关做逻辑判断
    （见 GatewayNode.safety_payload）。

    报文底稿直接取 ICD 里的 SAFETY_EVENT 示例，避免手写必填字段跟不上 Schema。
    """
    from patrol.tools import validate as V
    rep = next(b for b in V._icd_json_blocks()
               if b and b.get("msg_type") == "STATUS_REPORT"
               and b.get("report_kind") == "SAFETY_EVENT")
    rep = json.loads(json.dumps(rep))                 # 别改到别人的底稿

    rep["safety"]["brake_latency_ms"] = 150
    M.validate(rep, "STATUS_REPORT")                  # 150 ms 必须放行
    assert 150 > L.BRAKE_LATENCY_LIMIT_MS             # 而判据仍然是 100 ms

    rep["safety"]["brake_latency_ms"] = -1            # 负数物理上不可能，仍该拦下
    with pytest.raises(M.SchemaViolation):
        M.validate(rep, "STATUS_REPORT")


def test_gateway_annotates_brake_latency_breach(gw):
    """超标时网关要把判据写进 detail，让证据自带结论而不是只剩一个裸数字。"""
    ev = {"event_type": "OBSTACLE", "severity": "CRITICAL",
          "source": "CHASSIS_SAFETY_LAYER", "action_taken": "BRAKE",
          "brake_latency_ms": 150, "detail": "前方 0.4 m 障碍"}
    out = gw.safety_payload(ev)
    assert out["brake_latency_ms"] == 150
    assert "超过验收指标 100 ms" in out["detail"], out["detail"]
    assert "前方 0.4 m 障碍" in out["detail"], "原始描述不能被判据覆盖掉"

    ev["brake_latency_ms"] = 95            # 达标时不该加判据
    assert "超过验收指标" not in gw.safety_payload(ev)["detail"]

    ev["brake_latency_ms"] = None          # 无实测值（如看门狗事件）也不该炸
    assert gw.safety_payload(ev)["brake_latency_ms"] is None


# ---------------------------------------------------------------- D3 决议 A1
def test_ptz_rate_is_in_schema_but_still_gated_by_the_switch(gw):
    """A1 落地：`PTZ_RATE` 进了 Schema，但开关关掉时网关仍须拒绝。

    「增删白名单指令」按 ICD §2.5 是要重新评审安全边界的改动。Schema 里有它，
    不等于网关一定接受它——这两件事分开，正是那条规矩的落点。
    """
    cmd = mk("PTZ_RATE", {"pan_dps": 12.0, "tilt_dps": -3.0, "ttl_ms": 300})
    M.validate(cmd, "CONTROL_COMMAND")        # Schema 认它（v1.0 时会被拦下）

    gw.allow_rate = True
    assert gw.handle_command(cmd)["result"] == "ACCEPTED"

    gw.allow_rate = False
    ack = gw.handle_command(mk("PTZ_RATE", {"pan_dps": 12.0, "tilt_dps": -3.0,
                                            "ttl_ms": 300}))
    assert ack["result"] == "REJECTED"
    assert ack["reject_code"] == "NOT_IN_WHITELIST", ack
    gw.allow_rate = True
