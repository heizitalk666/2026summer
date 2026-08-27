"""指令实时流。老师的要求原话是"把给小车和云台的指令直接显示出来就行"。

**显示层的错误特别隐蔽**：它不会崩、不会让测试变红，只会让人看着一屏
看起来很正常的字，得出错误的结论。所以这里钉的是三件容易出错、错了又
看不出来的事：

1. 翻译不能失真——单位、正负号、被拒的原因
2. 被拒的指令必须显眼，而且要说清是六项检查里的哪一项没过
3. 控制台先于系统启动是常态（演示时必然如此），文件不存在不能当错误
"""
from __future__ import annotations

import json
import os

import pytest

from patrol.common.config import Config
from patrol.tools.console import (AuditTail, Console, Line, Snapshot, describe,
                                  failed_checks, render, status_line)


def rec(command="PTZ_SET", params=None, result="ACCEPTED", **kw):
    base = {"ts_utc_ms": 1_700_000_000_000, "cmd_id": "c1", "run_id": "r1",
            "issued_by": "MISSION_FSM", "command": command,
            "params": params if params is not None else {},
            "result": result, "reject_code": None, "reject_detail": None,
            "checks": {}, "handle_us": 900}
    base.update(kw)
    return base


# ---------------------------------------------------------------- 翻译
def test_ptz_set_shows_units_on_every_number():
    """**调云台时最容易犯的错就是把弧度当度、把 zoom 当焦距。**

    参数表里写着 2.4 谁也说不清是什么，写成 zoom=2.40× 就没有歧义。
    """
    l = describe(rec(params={"pan_deg": -88.3, "tilt_deg": -2.1, "zoom": 2.4,
                             "speed": "NORMAL"}))
    assert l.target == "云台"
    assert "pan=-88.3°" in l.text and "tilt=-2.1°" in l.text and "zoom=2.40×" in l.text


def test_signs_survive_the_translation():
    """正负号丢了就分不清云台往左还是往右转——这是排查对准问题的第一线索。"""
    assert "pan=+90.0°" in describe(rec(params={"pan_deg": 90.0})).text
    assert "pan=-90.0°" in describe(rec(params={"pan_deg": -90.0})).text


@pytest.mark.parametrize("command,params,want", [
    ("PAUSE", {"reason": "VERIFY_REQUEST"}, "暂停"),
    ("RESUME", {}, "恢复巡航"),
    ("CREEP_FORWARD", {"distance_m": 0.30}, "0.30 m"),
    ("GOTO_OBSERVE", {"x_m": 12.4, "y_m": -3.2, "yaw_deg": 0.0}, "12.40"),
    ("PTZ_RATE", {"pan_dps": -14.2, "tilt_dps": 0.4}, "pan=-14.2°/s"),
    ("HEARTBEAT", {"mission_state": "CRUISE"}, "CRUISE"),
])
def test_every_whitelisted_command_reads_as_chinese(command, params, want):
    """六条白名单指令每条都要有人话。漏一条就会在流水里冒出一行生 JSON。"""
    l = describe(rec(command=command, params=params))
    assert want in l.text and l.target in ("小车", "云台", "心跳")


def test_a_command_outside_the_whitelist_still_renders():
    """网关会拒掉白名单外的东西，但显示层不能因此抛异常——那会把控制台带走。"""
    l = describe(rec(command="SET_WHEEL_TORQUE", params={"nm": 12},
                     result="REJECTED", reject_code="NOT_IN_WHITELIST"))
    assert l.ok is False and l.text and "NOT_IN_WHITELIST" in l.detail


# ---------------------------------------------------------------- 被拒
def test_rejected_command_names_the_failing_check():
    """**这是三层安全边界唯一可观测的地方。**

    ICD §4.6 要求网关逐项上报六项检查，正是为了让"有没有在校验"看得见。
    只显示"被拒了"而不说是哪一项拒的，等于把这个设计浪费掉。
    """
    l = describe(rec(params={"pan_deg": 200.0}, result="REJECTED",
                     reject_code="OUT_OF_RANGE", reject_detail="pan 超限",
                     checks={"range": "FAIL", "rate": "PASS", "state": "PASS"}))
    assert l.ok is False
    assert "OUT_OF_RANGE" in l.detail and "range" in l.detail


def test_failed_checks_ignores_pass_and_skip():
    assert failed_checks({"checks": {"a": "PASS", "b": "FAIL", "c": "SKIP",
                                     "d": "FAIL"}}) == ["b", "d"]
    assert failed_checks({}) == []


def test_rejections_are_visually_distinct():
    """一行红字混在几百行白字里才挑得出来；不上色时也要有 ✗。"""
    bad = render(describe(rec(result="REJECTED", reject_code="X")),
                 t0_ms=1_700_000_000_000, color=False)
    good = render(describe(rec()), t0_ms=1_700_000_000_000, color=False)
    assert "✗" in bad and "✓" in good
    colored = render(describe(rec(result="REJECTED", reject_code="X")),
                     t0_ms=1_700_000_000_000, color=True)
    assert "\033[" in colored


def test_render_uses_relative_time_so_the_eye_can_follow():
    l = describe(rec(ts_utc_ms=1_700_000_012_500))
    assert "t=   12.5s" in render(l, t0_ms=1_700_000_000_000, color=False)


# ---------------------------------------------------------------- 跟读
def test_tail_waits_for_a_file_that_does_not_exist_yet(tmp_path):
    """**演示时的顺序一定是先开控制台再开系统**，那时文件还不存在。

    第一版直接 open() 抛 FileNotFoundError 退出，得让人先跑一遍再开控制台。
    """
    f = tmp_path / "audit.jsonl"
    t = AuditTail(f)
    assert t.poll() == []                      # 不抛异常，等着
    f.write_text(json.dumps(rec()) + "\n", encoding="utf-8")
    got = t.poll()
    assert len(got) == 1 and got[0]["command"] == "PTZ_SET"
    t.close()


def test_tail_only_returns_new_records(tmp_path):
    f = tmp_path / "audit.jsonl"
    f.write_text(json.dumps(rec()) + "\n", encoding="utf-8")
    t = AuditTail(f)                            # 默认从文件末尾开始
    assert t.poll() == []
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec(command="PAUSE")) + "\n")
    assert [r["command"] for r in t.poll()] == ["PAUSE"]
    t.close()


def test_tail_from_start_replays_everything(tmp_path):
    f = tmp_path / "audit.jsonl"
    f.write_text("".join(json.dumps(rec(command=c)) + "\n"
                         for c in ("PAUSE", "RESUME")), encoding="utf-8")
    t = AuditTail(f, from_start=True)
    assert [r["command"] for r in t.poll()] == ["PAUSE", "RESUME"]
    t.close()


def test_tail_recovers_when_the_file_is_replaced(tmp_path):
    """每轮巡检换一个 run_id，日志可能被换掉；控制台不该就此哑掉。"""
    f = tmp_path / "audit.jsonl"
    f.write_text(json.dumps(rec()) + "\n", encoding="utf-8")
    t = AuditTail(f, from_start=True)
    t.poll()
    os.remove(f)
    f.write_text(json.dumps(rec(command="RESUME")) + "\n", encoding="utf-8")
    assert any(r["command"] == "RESUME" for r in t.poll())
    t.close()


def test_a_half_written_line_is_not_fatal(tmp_path):
    """网关 flush 与控制台读取之间总有一瞬间会读到半行。"""
    f = tmp_path / "audit.jsonl"
    f.write_text(json.dumps(rec()) + "\n" + '{"command": "PAU', encoding="utf-8")
    t = AuditTail(f, from_start=True)
    assert len(t.poll()) == 1
    t.close()


# ---------------------------------------------------------------- 状态
def test_snapshot_tracks_pose_and_ptz():
    s = Snapshot()
    s.update_status({"ts_utc_ms": 5, "pose": {"x_m": 12.4, "y_m": -3.18,
                                              "yaw_deg": 180.0},
                     "chassis": {"state": "STOPPED", "speed_mps": 0.0,
                                 "current_waypoint_id": "WP-07",
                                 "battery_pct": 91.0},
                     "ptz": {"pan_deg": -88.3, "tilt_deg": -2.1, "zoom": 2.4,
                             "hfov_deg": 25.9}})
    line = status_line(s, color=False)
    assert "12.40" in line and "WP-07" in line and "2.40×" in line


def test_safety_intervention_is_never_dimmed():
    """安全层介入是最该被看见的一件事，不能和普通状态一样淡出去。"""
    s = Snapshot()
    s.update_status({"chassis": {"safety_layer_active": True}, "pose": {},
                     "ptz": {}})
    assert "安全层已介入" in status_line(s, color=False)


def test_snapshot_survives_a_partial_status_report():
    Snapshot().update_status({"pose": {}, "ptz": {}, "chassis": {}})


# ---------------------------------------------------------------- 只读
def test_console_step_is_non_blocking_and_read_only(tmp_path):
    """**控制台不发指令、不改状态、不参与判决。**

    多开几个、开在跑到一半、跑挂了重开，对系统都不该有任何影响。
    """
    f = tmp_path / "audit.jsonl"
    f.write_text(json.dumps(rec(command="PAUSE")) + "\n", encoding="utf-8")
    cfg = Config.load(overrides={"logging": {"dir": str(tmp_path)},
                                 "gateway": {"audit_log": str(f)}})
    c = Console(cfg, from_start=True)
    import time
    t0 = time.monotonic()
    lines = c.step()                            # 没开总线也要能跑
    assert time.monotonic() - t0 < 0.2
    assert [l.command for l in lines] == ["PAUSE"]
    c.close()


def test_accepted_heartbeats_are_hidden_by_default(tmp_path):
    """心跳 5 Hz，一轮三千多条，混在指令流里会把真正的指令淹掉。

    但**被拒的心跳要显示**——那说明看门狗出了问题，是真事。
    """
    f = tmp_path / "audit.jsonl"
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec(command="HEARTBEAT")) + "\n")
        fh.write(json.dumps(rec(command="HEARTBEAT", result="REJECTED",
                                reject_code="STATE_CONFLICT")) + "\n")
        fh.write(json.dumps(rec(command="PAUSE")) + "\n")
    cfg = Config.load(overrides={"logging": {"dir": str(tmp_path)},
                                 "gateway": {"audit_log": str(f)}})
    c = Console(cfg, from_start=True)
    got = [l.command for l in c.step()]
    assert got == ["HEARTBEAT", "PAUSE"], got
    c.close()


def test_push_failure_never_blocks_the_display(tmp_path):
    """云端没起、网断了，终端也必须照常刷。"""
    f = tmp_path / "audit.jsonl"
    f.write_text(json.dumps(rec()) + "\n", encoding="utf-8")
    cfg = Config.load(overrides={"logging": {"dir": str(tmp_path)},
                                 "gateway": {"audit_log": str(f)}})
    c = Console(cfg, from_start=True, push_url="http://127.0.0.1:1")
    c.push(c.step())                            # 端口 1 必然连不上
    c.close()


def test_line_round_trips_to_the_web_payload():
    """终端与网页共用同一份翻译，避免两处分叉说出不同的话。"""
    d = describe(rec(params={"pan_deg": 1.0})).as_dict()
    assert set(d) >= {"ts_utc_ms", "target", "text", "ok", "latency_ms", "command"}
    json.dumps(d)                               # 必须可 JSON 化
