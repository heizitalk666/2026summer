"""复核状态机：无死路、抑制生效、安全事件 200 ms 中止、预算调度。

方案书 §7.4「可验证性」的第三件事——注入安全事件时正在进行的测量在 200 ms
内中止——在这里验证。
"""
from __future__ import annotations

import time

import pytest

from patrol.common.clock import mono_ns
from patrol.common.config import Config
from patrol.mission.budget import build_budget
from patrol.mission.fsm import TIMEOUT_TO, MissionFSM, State
from patrol.mission.servo import GimbalServo
from patrol.mission.suppress import build_suppression


def make_fsm(cfg, **kw):
    return MissionFSM(cfg, budget=build_budget(cfg), suppress=build_suppression(cfg),
                      servo=GimbalServo(cfg), **kw)


def det_event(*, is_suspect=True, track_id=7, stage="CRUISE", zoom=1.0,
              pose=(10.0, -3.18), pose_valid=True, conf=0.41, p=49.9,
              rule="CONF_BAND", cx=1200.0, cy=600.0):
    return {
        "schema_version": "1.1.0", "msg_type": "DETECTION_EVENT", "seq": 1,
        "ts_mono_ns": mono_ns(), "ts_utc_ms": 1, "run_id": "20260901-093012-a7f3",
        "event_id": "3f2b9c14-7d5e-4a81-b0c6-2e9f1a4d8e77" if is_suspect else None,
        "stage": stage,
        "model": {"name": "yolo11s", "input_w": 640, "input_h": 640, "quant": "INT8",
                  "conf_threshold": 0.25, "nms_iou": 0.45},
        "context": {"waypoint_id": "WP-07",
                    "pose": {"x_m": pose[0], "y_m": pose[1], "yaw_deg": 0.0,
                             "cov_trace": 0.014},
                    "pose_valid": pose_valid, "speed_mps": 0.0,
                    "ptz": {"pan_deg": 90.0, "tilt_deg": 2.0, "zoom": zoom,
                            "hfov_deg": 60.0 / zoom},
                    "image_w": 1920, "image_h": 1080},
        "detections": [{"track_id": track_id, "defect_class": "PRESSURE_GAUGE",
                        "confidence": conf, "bbox": [cx - 25, cy - 25, cx + 25, cy + 25],
                        "target_size_m": 0.15, "est_distance_m": 5.0,
                        "pixel_density_px": p,
                        "aim_offset": {"pan_deg": -4.2, "tilt_deg": 1.6},
                        "l2_reading": None}],
        "suspect": {"is_suspect": is_suspect, "trigger_rule": rule if is_suspect else None,
                    "target_track_id": track_id if is_suspect else None,
                    "severity": 0.7, "novelty": 1.0, "priority": 0.287,
                    "suppressed_by": None},
        "latency_ms": {"capture_to_infer": 12, "infer": 38, "postproc": 9, "total": 63},
    }


def status(*, chassis="MOVING", at_target=True, focus="LOCKED", kind="PERIODIC",
           safety=None, moving=False):
    """默认 at_target=True：巡航稳态下云台本就停在巡航姿态。

    状态机不接受"云台正在扫转时"的触发（扫转中的帧有运动模糊，且目标很快
    会扫出画面），所以需要模拟扫转的用例要显式传 at_target=False。
    """
    return {
        "schema_version": "1.1.0", "msg_type": "STATUS_REPORT", "seq": 1,
        "ts_mono_ns": mono_ns(), "ts_utc_ms": 1, "run_id": "20260901-093012-a7f3",
        "report_kind": kind,
        "chassis": {"state": chassis, "speed_mps": 0.0, "path_progress": 0.3,
                    "distance_to_goal_m": None, "current_waypoint_id": "WP-07",
                    "battery_pct": 80.0, "safety_layer_active": safety is not None},
        "ptz": {"pan_deg": 90.0, "tilt_deg": 2.0, "zoom": 1.0, "hfov_deg": 60.0,
                "moving": moving, "focus_state": focus, "at_target": at_target},
        "pose": {"x_m": 10.0, "y_m": -3.18, "yaw_deg": 0.0, "cov_trace": 0.014,
                 "valid": True, "source": "LIDAR_SLAM"},
        "watchdog": {"heartbeat_ok": True, "last_heartbeat_age_ms": 100,
                     "watchdog_triggered": False},
        "exec": None, "safety": safety,
    }


def drive_aim(f, *, cx=960.0, cy=540.0, limit=12):
    """把状态机从 AIM 推到下一个状态。

    AIM 现在分两步：先下发一条 PTZ_SET 把 aim_offset 一次给足（前馈），等云台
    走位置环到位，再进速率闭环收残差。所以要先喂一拍"云台在动"，再喂"到位"，
    否则状态机会一直等前馈那一步，直到 500 ms 兜底才开闭环——单元测试里瞬间
    跑完这几拍，那 500 ms 永远等不到。
    """
    f.tick(detection=det_event(cx=cx, cy=cy),
           status=status(chassis="STOPPED", at_target=False, moving=True))
    for _ in range(limit):
        f.tick(detection=det_event(cx=cx, cy=cy), status=status(chassis="STOPPED"))
        if f.state is not State.AIM:
            return
    raise AssertionError("AIM 在 %d 拍内没有退出" % limit)


# ---------------------------------------------------------------- 结构
def test_every_state_has_an_exit():
    """状态图里不存在没有出边的节点，也不存在只能靠外部干预才能离开的状态。"""
    for s in State:
        if s is State.CRUISE:
            continue
        assert s in TIMEOUT_TO, "%s 没有超时出边" % s
        assert TIMEOUT_TO[s] in (State.ABORT, State.CRUISE, State.RESUME)


def test_ten_states_match_icd(cfg):
    from patrol.gateway import limits as L
    assert {s.value for s in State} == set(L.MISSION_STATES)


# ---------------------------------------------------------------- 流程
def test_needs_n_frames_to_confirm(cfg):
    """连续三帧确认，挡住单帧噪声（差异清单 C10）。"""
    f = make_fsm(cfg)
    n = int(cfg.get("mission.fsm.suspect_confirm_frames"))
    for i in range(n - 1):
        f.tick(detection=det_event(), status=status())
        assert f.state is State.CRUISE
    f.tick(detection=det_event(), status=status())
    assert f.state is State.SUSPECT


def test_full_happy_path(cfg):
    """CRUISE → … → RESUME → CRUISE，走完一次完整复核。"""
    packed = {}
    f = make_fsm(cfg, capture_cb=lambda ctx: True,
                 pack_cb=lambda ctx: packed.setdefault("ok", True))
    for _ in range(3):
        f.tick(detection=det_event(), status=status())
    assert f.state is State.SUSPECT
    cmds = f.tick(detection=det_event(), status=status())
    assert f.state is State.HALT_REQ
    assert cmds[0].command == "PAUSE"

    f.tick(status=status(chassis="STOPPED"))
    assert f.state is State.AIM
    # 目标已在画面中心 → 伺服判定到位
    drive_aim(f)
    assert f.state is State.ZOOM
    f.tick(status=status(chassis="STOPPED", at_target=True, focus="LOCKED"))
    assert f.state is State.CAPTURE
    f.tick(status=status(chassis="STOPPED"))
    assert f.state is State.VERIFY
    f.tick(detection=det_event(stage="VERIFY", zoom=3.0, conf=0.91, p=149.5),
           status=status(chassis="STOPPED"))
    assert f.state is State.PACK
    cmds = f.tick(status=status(chassis="STOPPED"))
    assert f.state is State.RESUME
    assert [c.command for c in cmds] == ["PTZ_SET", "RESUME"]
    assert packed.get("ok")
    f.tick(status=status(chassis="MOVING"))
    assert f.state is State.CRUISE
    assert f.stats["verify_done"] == 1


def test_before_and_after_snapshots_recorded(cfg):
    """证据包的 before/after 结构相同，方便直接做差算增益。"""
    seen = {}
    f = make_fsm(cfg, capture_cb=lambda ctx: True,
                 pack_cb=lambda ctx: seen.update(before=ctx.before, after=ctx.after) or True)
    for _ in range(3):
        f.tick(detection=det_event(), status=status())
    f.tick(detection=det_event(), status=status())
    f.tick(status=status(chassis="STOPPED"))
    drive_aim(f)
    f.tick(status=status(chassis="STOPPED", at_target=True))
    f.tick(status=status(chassis="STOPPED"))
    f.tick(detection=det_event(stage="VERIFY", zoom=3.0, conf=0.91, p=149.5),
           status=status(chassis="STOPPED"))
    f.tick(status=status(chassis="STOPPED"))
    assert set(seen["before"]) == set(seen["after"])
    assert seen["after"]["confidence"] > seen["before"]["confidence"]
    assert seen["after"]["pixel_density_px"] > seen["before"]["pixel_density_px"]


# ---------------------------------------------------------------- 安全
def test_safety_event_aborts_within_200ms(cfg):
    """方案书 §9.3：注入安全事件，200 ms 内中止当前测量。"""
    f = make_fsm(cfg, capture_cb=lambda ctx: True)
    for _ in range(3):
        f.tick(detection=det_event(), status=status())
    f.tick(detection=det_event(), status=status())
    f.tick(status=status(chassis="STOPPED"))
    assert f.state is State.AIM

    t0 = time.perf_counter()
    cmds = f.tick(status=status(kind="SAFETY_EVENT", safety={
        "event_type": "OBSTACLE_DETECTED", "severity": "CRITICAL",
        "source": "CHASSIS_SAFETY_LAYER", "action_taken": "BRAKE",
        "brake_latency_ms": 68, "detail": "front lidar sector 3"}))
    dt_ms = (time.perf_counter() - t0) * 1000.0
    assert f.state is State.ABORT
    assert dt_ms < 200.0, "中止耗时 %.1f ms" % dt_ms
    assert [c.command for c in cmds] == ["PTZ_SET", "RESUME"]
    assert f.ctx.abort["reason"] == "SAFETY_EVENT"


def test_estop_aborts(cfg):
    f = make_fsm(cfg)
    for _ in range(3):
        f.tick(detection=det_event(), status=status())
    f.tick(detection=det_event(), status=status())
    f.tick(status=status(chassis="ESTOP"))
    assert f.state is State.ABORT
    assert f.ctx.abort["reason"] == "ESTOP"


def test_abort_still_produces_evidence(cfg):
    """中止的复核照样打包上传，因为复核失败的样本对调参最有价值。"""
    f = make_fsm(cfg)
    for _ in range(3):
        f.tick(detection=det_event(), status=status())
    f.tick(detection=det_event(), status=status())
    f.tick(status=status(chassis="ESTOP"))
    assert f.ctx is not None and f.ctx.abort is not None
    assert f.ctx.abort["at_state"] == "HALT_REQ"


# ---------------------------------------------------------------- 抑制
@pytest.mark.parametrize("case,kw,want", [
    ("定位失锁", dict(pose_valid=False), "POSE_INVALID"),
])
def test_suppression_blocks_verify(cfg, case, kw, want):
    f = make_fsm(cfg)
    for _ in range(3):
        f.tick(detection=det_event(**kw), status=status())
    f.tick(detection=det_event(**kw), status=status())
    assert f.state is State.CRUISE, case
    assert f.stats["suppressed"].get(want) == 1


def test_track_cooldown_after_verify(cfg):
    f = make_fsm(cfg, capture_cb=lambda ctx: True, pack_cb=lambda ctx: True)
    f.suppress.on_verify_done(7, (10.0, -3.18))
    for _ in range(4):
        f.tick(detection=det_event(track_id=7), status=status())
    assert f.state is State.CRUISE
    assert f.stats["suppressed"].get("TRACK_COOLDOWN") == 1


def test_waypoint_once_catches_new_track_id(cfg):
    """跟踪断链后同一目标以新 track_id 再触发，靠巡检位规则兜住。"""
    f = make_fsm(cfg)
    f.suppress.on_verify_done(7, (10.0, -3.18))
    for _ in range(4):
        f.tick(detection=det_event(track_id=999), status=status())
    assert f.stats["suppressed"].get("WAYPOINT_ONCE") == 1


def test_budget_exhausted_defers_by_priority(cfg):
    f = make_fsm(cfg)
    while not f.budget.exhausted():
        f.budget.consume()
    for _ in range(4):
        f.tick(detection=det_event(track_id=1234), status=status())
    assert f.stats["suppressed"].get("BUDGET_EXHAUSTED") == 1
    assert f.budget.deferred_count() == 1


# ---------------------------------------------------------------- 超时
def test_halt_req_timeout_goes_to_abort(cfg, monkeypatch):
    c = Config.load(overrides={"mission": {"fsm": {"timeout_s": {"HALT_REQ": 0.05}}}})
    f = make_fsm(c)
    for _ in range(4):
        f.tick(detection=det_event(), status=status())
    assert f.state is State.HALT_REQ
    time.sleep(0.08)
    f.tick(status=status())          # 底盘一直没停下
    assert f.state is State.ABORT
    assert f.ctx.abort["reason"] == "STATE_TIMEOUT"


def test_pack_timeout_still_resumes(cfg):
    """PACK 超时记 PACK_FAILED，但仍转 RESUME——不能因为打包失败把车撂在路上。"""
    c = Config.load(overrides={"mission": {"fsm": {"timeout_s": {"PACK": 0.05}}}})
    f = make_fsm(c, capture_cb=lambda ctx: True)
    f.ctx = type(f.ctx or object(), (), {})() if False else None
    # 直接把状态机推到 PACK
    for _ in range(4):
        f.tick(detection=det_event(), status=status())
    f.tick(status=status(chassis="STOPPED"))
    drive_aim(f)
    f.tick(status=status(chassis="STOPPED", at_target=True))
    f.tick(status=status(chassis="STOPPED"))
    f.tick(detection=det_event(stage="VERIFY", zoom=3.0), status=status(chassis="STOPPED"))
    assert f.state is State.PACK
    time.sleep(0.08)
    f.tick(status=status(chassis="STOPPED"))
    assert f.state is State.RESUME


def test_no_trigger_while_gimbal_is_slewing(cfg):
    """云台扫转中不接受触发。

    车折返时巡航指向要从 +90° 摆到 -90°，180° 摆幅按 60°/s 要 3 秒；这期间
    目标一闪而过，据此发起的复核等进到 AIM 时目标早已扫走，只能干等超时。
    实测这是"复核全部失败"的直接原因。
    """
    f = make_fsm(cfg)
    for _ in range(6):
        f.tick(detection=det_event(), status=status(at_target=False))
    assert f.state is State.CRUISE, "扫转中不该触发"
    for _ in range(3):
        f.tick(detection=det_event(), status=status(at_target=True))
    assert f.state is State.SUSPECT, "云台停稳后应正常触发"


def test_aim_does_not_hop_between_two_same_class_targets(cfg):
    """AIM 期间同类目标不止一个时，伺服必须咬住一个，不能来回改判。

    柜面上两块压力表相距 2.23 m，在 5 m 处约 770 px，会同时落在画面里。
    只按"离画面中心最近"重认，云台摆到两者中间时就会左右横跳——实测速率
    指令是 +17.6 / +20.5 / −14.2 / −21.3 / +12.8 / −23.1 °/s 这样一串，
    三秒内进不了死区，最后超时 ABORT。
    """
    f = make_fsm(cfg, capture_cb=lambda ctx: True)
    for _ in range(4):
        f.tick(detection=det_event(track_id=7, cx=1200.0), status=status())
    f.tick(status=status(chassis="STOPPED"))
    assert f.state is State.AIM

    def two_targets(near_cx, far_cx, near_id, far_id):
        """两块同类表同时在画面里，跟踪每帧都换 id（云台在动，IoU 匹配不上）。"""
        ev = det_event(track_id=near_id, cx=near_cx)
        far = dict(ev["detections"][0])
        far.update(track_id=far_id, bbox=[far_cx - 25, 575.0, far_cx + 25, 625.0])
        ev["detections"] = [ev["detections"][0], far]
        ev["detections"][0]["bbox"] = [near_cx - 25, 575.0, near_cx + 25, 625.0]
        return ev

    f.tick(detection=two_targets(1000.0, 250.0, 90, 91),
           status=status(chassis="STOPPED", at_target=False, moving=True))
    picked = []
    for i in range(8):
        # 每帧都给新的 track_id，逼状态机走"按类别重认"那一支
        ev = two_targets(980.0, 240.0, 100 + i * 2, 101 + i * 2)
        f.tick(detection=ev, status=status(chassis="STOPPED"))
        d = f._reacquire(ev)                       # noqa: SLF001
        picked.append(None if d is None else round((d["bbox"][0] + d["bbox"][2]) / 2))
    assert set(x for x in picked if x is not None) == {980}, \
        "重认在两个目标之间来回跳：%s" % picked
