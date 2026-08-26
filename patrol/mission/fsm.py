"""复核状态机。ICD §7.2。

十个状态，每个状态都有独立超时且超时动作都指向 ABORT 或 CRUISE。
**状态图里不存在没有出边的节点，也不存在只能靠外部干预才能离开的状态**——
一次底盘失联或云台卡滞，不能让车永久停在巡检路线中间。

| 状态      | 发出                        | 等待条件                       | 超时动作 |
|-----------|-----------------------------|--------------------------------|----------|
| CRUISE    | 仅 HEARTBEAT                | suspect.is_suspect = true      | —        |
| SUSPECT   | 无                          | 三重抑制与预算检查通过         | 回 CRUISE|
| HALT_REQ  | PAUSE(VERIFY_REQUEST)       | chassis.state = STOPPED        | ABORT    |
| AIM       | PTZ_SET 或 PTZ_RATE 闭环    | 像素偏差进死区 / at_target     | ABORT    |
| ZOOM      | PTZ_SET(zoom)               | at_target 且 focus = LOCKED    | ABORT    |
| CAPTURE   | 无（走 ICamera）            | 帧抓取完成                     | ABORT    |
| VERIFY    | 无（走 perception）         | 收到 stage=VERIFY 的报文       | ABORT    |
| PACK      | 无（走 uploader）           | manifest 落盘完成              | 记失败仍转 RESUME |
| RESUME    | PTZ_SET(0,0,1) + RESUME     | chassis.state = MOVING         | 重发一次 |
| ABORT     | PTZ_SET(0,0,1) + RESUME     | chassis.state = MOVING         | 报 RESUME_FAILED |

**AIM 与 ZOOM 拆成两步不是随意的**：先在广角端把目标转到画面中心，再变焦。
反过来做的话，变焦后视场只有 20° 左右，转向时目标很容易划出画面，重新找回
来的代价远大于多发一条指令。

**ABORT 的出口动作和 RESUME 完全一致**，区别只在于是否产出证据包：ABORT 时
manifest.abort 非空，记录中止在哪个状态、原因是什么，gain.verify_success =
false。中止的复核照样打包上传，因为复核失败的样本对调参最有价值。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

import numpy as np

from patrol.common.clock import Deadline, mono_ns
from patrol.mission.budget import VerifyBudget
from patrol.mission.servo import GimbalServo
from patrol.mission.suppress import SuppressionState


class State(Enum):
    CRUISE = "CRUISE"
    SUSPECT = "SUSPECT"
    HALT_REQ = "HALT_REQ"
    AIM = "AIM"
    ZOOM = "ZOOM"
    CAPTURE = "CAPTURE"
    VERIFY = "VERIFY"
    PACK = "PACK"
    RESUME = "RESUME"
    ABORT = "ABORT"


#: 每个状态的超时动作。**没有一个状态的出边是空的。**
TIMEOUT_TO = {
    State.SUSPECT: State.CRUISE,
    State.HALT_REQ: State.ABORT,
    State.AIM: State.ABORT,
    State.ZOOM: State.ABORT,
    State.CAPTURE: State.ABORT,
    State.VERIFY: State.ABORT,
    State.PACK: State.RESUME,      # 记 PACK_FAILED，仍转 RESUME
    State.RESUME: State.ABORT,     # 重发一次后仍失败才 ABORT
    State.ABORT: State.CRUISE,     # 上报 RESUME_FAILED，由看门狗兜底
}


@dataclass
class Command:
    """状态机想发的一条指令。由 node 转成 IF-2 报文送网关。"""

    command: str
    params: dict
    timeout_ms: int = 2000
    issued_by: str = "MISSION_FSM"


@dataclass
class VerifyContext:
    """一次复核的全过程记录，最终变成证据包。"""

    event_id: str
    track_id: int | None = None
    defect_class: str | None = None
    waypoint_id: str | None = None
    trigger_rule: str | None = None
    before: dict | None = None
    after: dict | None = None
    pose_xy: tuple[float, float] | None = None
    frames: list = field(default_factory=list)
    aux_frames: list = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)
    abort: dict | None = None
    target_zoom: float = 3.0
    used_aux: bool = False
    started_ns: int = field(default_factory=mono_ns)


class MissionFSM:
    """状态机本体。不碰 ZeroMQ，也不碰驱动——只吃事件、吐指令。

    这样它可以在测试里被逐状态驱动，不需要起四个进程。
    """

    def __init__(self, cfg, *, budget: VerifyBudget, suppress: SuppressionState,
                 servo: GimbalServo,
                 capture_cb: Callable[[VerifyContext], bool] | None = None,
                 pack_cb: Callable[[VerifyContext], bool] | None = None):
        self.cfg = cfg
        self.budget = budget
        self.suppress = suppress
        self.servo = servo
        self.capture_cb = capture_cb
        self.pack_cb = pack_cb

        f = cfg.get("mission.fsm")
        self.timeout_s = dict(f.get("timeout_s"))
        self.budget_s = dict(f.get("budget_s"))
        self.confirm_frames = int(f.get("suspect_confirm_frames", 3))
        cap = cfg.get("mission.capture")
        self.capture_mode = str(cap.get("mode", "conditional")).lower()
        self.highlight_trigger = float(cap.get("highlight_trigger", 0.12))
        self.aux_offset = float(cap.get("aux_offset_deg", 15.0))
        self.servo_mode = str(cfg.get("mission.servo.mode", "pid")).lower()
        self.rate_ttl_ms = int(cfg.get("mission.servo.rate_ttl_ms", 300))
        self.p_min = float(cfg.get("perception.quality.pixel_density_target", 120.0))
        self.max_zoom = float(cfg.get("optics.max_zoom", 3.0))
        self.cruise_ptz = dict(cfg.get("mission.cruise_ptz"))

        self.state = State.CRUISE
        self.ctx: VerifyContext | None = None
        self._deadline: Deadline | None = None
        self._entered_ns = mono_ns()
        self._confirm = 0
        self._resume_retried = False
        self._last_status: dict | None = None
        self._last_det: dict | None = None
        self.stats = {"verify_started": 0, "verify_done": 0, "aborted": 0,
                      "suppressed": {}}
        self.on_transition: Callable[[State, State, str], None] | None = None

    # ------------------------------------------------------------ 转移
    def _goto(self, nxt: State, reason: str = "") -> None:
        prev = self.state
        if self.ctx is not None and prev not in (State.CRUISE,):
            self.ctx.timeline.append(
                {"state": prev.value,
                 "duration_ms": max(0, int((mono_ns() - self._entered_ns) // 1_000_000))})
        self.state = nxt
        self._entered_ns = mono_ns()
        t = self.timeout_s.get(nxt.value)
        self._deadline = Deadline(t * 1000.0) if t else None
        if nxt is State.AIM:
            self.servo.reset()
        if nxt is State.RESUME:
            self._resume_retried = False
        if self.on_transition:
            self.on_transition(prev, nxt, reason)

    def _abort(self, reason: str, detail: str = "") -> list[Command]:
        if self.ctx is not None and self.ctx.abort is None:
            self.ctx.abort = {"at_state": self.state.value, "reason": reason,
                              "detail": (detail or reason)[:256]}
        self.stats["aborted"] += 1
        self._goto(State.ABORT, reason)
        return self._home_and_resume()

    def _home_and_resume(self) -> list[Command]:
        """ABORT 与 RESUME 的出口动作完全一致：云台归位 + 恢复巡检。"""
        return [
            Command("PTZ_SET", {"pan_deg": float(self.cruise_ptz.get("pan_deg", 0.0)),
                                "tilt_deg": float(self.cruise_ptz.get("tilt_deg", 0.0)),
                                "zoom": float(self.cruise_ptz.get("zoom", 1.0)),
                                "speed": "NORMAL"}, timeout_ms=3000),
            Command("RESUME", {}, timeout_ms=2000),
        ]

    # ------------------------------------------------------------ 主入口
    def tick(self, *, detection: dict | None = None,
             status: dict | None = None) -> list[Command]:
        """喂一拍事件，返回要发的指令。"""
        if status is not None:
            self._last_status = status
            # 安全事件优先于一切：正在进行的复核必须在 200 ms 内中止
            if status.get("report_kind") == "SAFETY_EVENT":
                sev = (status.get("safety") or {}).get("severity")
                if sev == "CRITICAL" and self.state not in (
                        State.CRUISE, State.ABORT, State.RESUME):
                    return self._abort("SAFETY_EVENT",
                                       (status.get("safety") or {}).get("detail", ""))
        if detection is not None:
            self._last_det = detection

        if self._deadline is not None and self._deadline.expired():
            return self._on_timeout()

        handler = getattr(self, "_st_" + self.state.value.lower())
        return handler() or []

    def _on_timeout(self) -> list[Command]:
        st = self.state
        nxt = TIMEOUT_TO.get(st, State.CRUISE)
        if st is State.RESUME and not self._resume_retried:
            # RESUME 超时先重发一次，仍失败才 ABORT
            self._resume_retried = True
            self._deadline = Deadline(self.timeout_s.get("RESUME", 1.0) * 1000.0)
            return self._home_and_resume()
        if nxt is State.ABORT:
            return self._abort("STATE_TIMEOUT", "%s 状态超时" % st.value)
        if st is State.PACK:
            if self.ctx is not None and self.ctx.abort is None:
                self.ctx.abort = {"at_state": "PACK", "reason": "DRIVER_ERROR",
                                  "detail": "PACK_FAILED: manifest 落盘超时"}
            self._goto(State.RESUME, "PACK_FAILED")
            return self._home_and_resume()
        self._goto(nxt, "TIMEOUT")
        if nxt is State.CRUISE:
            self.ctx = None
        return []

    # ------------------------------------------------------------ 各状态
    def _st_cruise(self) -> list[Command]:
        d = self._last_det
        if d is None or not d.get("suspect", {}).get("is_suspect"):
            self._confirm = 0
            return []
        self._confirm += 1
        if self._confirm < self.confirm_frames:
            return []          # 连续 N 帧确认，挡住单帧噪声
        self._confirm = 0
        s = d["suspect"]
        self.ctx = VerifyContext(
            event_id=d.get("event_id") or "",
            track_id=s.get("target_track_id"),
            trigger_rule=s.get("trigger_rule"),
            waypoint_id=(d.get("context") or {}).get("waypoint_id"))
        det = self._pick_detection(d, s.get("target_track_id"))
        if det is not None:
            self.ctx.defect_class = det.get("defect_class")
            self.ctx.before = _snapshot(det, (d["context"]["ptz"]["zoom"]))
            # 按需变焦（差异清单 C4）：算出刚好达到 120 px 判据的倍率，
            # 而不是一律顶到 3×——固定 3× 对近距离目标会过度放大导致出框
            p = float(det.get("pixel_density_px", 0.0))
            z = float(d["context"]["ptz"]["zoom"])
            from patrol.scene.optics import zoom_for_density
            self.ctx.target_zoom = zoom_for_density(z, p, self.p_min, self.max_zoom)
        pose = (d.get("context") or {}).get("pose") or {}
        self.ctx.pose_xy = (float(pose.get("x_m", 0.0)), float(pose.get("y_m", 0.0)))
        self._goto(State.SUSPECT, s.get("trigger_rule") or "")
        return []

    def _st_suspect(self) -> list[Command]:
        d = self._last_det or {}
        ctx = self.ctx
        pose_valid = bool((d.get("context") or {}).get("pose_valid", True))
        why = self.suppress.check(track_id=ctx.track_id if ctx else None,
                                  pose_xy=ctx.pose_xy if ctx else None,
                                  pose_valid=pose_valid)
        if why is None and self.budget.exhausted():
            why = "BUDGET_EXHAUSTED"
            self.budget.defer(d)
        if why is not None:
            self.stats["suppressed"][why] = self.stats["suppressed"].get(why, 0) + 1
            self.ctx = None
            self._goto(State.CRUISE, why)
            return []
        self.budget.consume()
        self.stats["verify_started"] += 1
        self._goto(State.HALT_REQ, "")
        return [Command("PAUSE", {"reason": "VERIFY_REQUEST"}, timeout_ms=4000)]

    def _st_halt_req(self) -> list[Command]:
        st = self._last_status
        if st is None:
            return []
        # STOPPING 与 STOPPED 必须区分：收到 STOPPING 继续等
        if st["chassis"]["state"] == "STOPPED":
            self._goto(State.AIM, "")
            return self._aim_commands(first=True)
        if st["chassis"]["state"] == "ESTOP":
            return self._abort("ESTOP", "急停生效，不尝试恢复")
        return []

    def _aim_commands(self, *, first: bool = False) -> list[Command]:
        """AIM：先在广角端把目标转到画面中心，再变焦。"""
        d = self._last_det
        det = self._pick_detection(d, self.ctx.track_id if self.ctx else None)
        if det is None:
            return []
        if self.servo_mode == "open_loop":
            # ICD v1.0 原样：一次 PTZ_SET + 等 at_target
            ptz = d["context"]["ptz"]
            off = det.get("aim_offset", {})
            return [Command("PTZ_SET", {
                "pan_deg": float(np.clip(ptz["pan_deg"] + off.get("pan_deg", 0.0), -170, 170)),
                "tilt_deg": float(np.clip(ptz["tilt_deg"] + off.get("tilt_deg", 0.0), -30, 60)),
                "zoom": 1.0, "speed": "NORMAL"}, timeout_ms=3000)]
        # A1 推荐：速率闭环
        bbox = det["bbox"]
        cx, cy = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
        pan_dps, tilt_dps = self.servo.step(cx, cy, zoom=float(d["context"]["ptz"]["zoom"]))
        return [Command("PTZ_RATE", {"pan_dps": round(float(pan_dps), 3),
                                     "tilt_dps": round(float(tilt_dps), 3),
                                     "ttl_ms": self.rate_ttl_ms}, timeout_ms=1000)]

    def _st_aim(self) -> list[Command]:
        st = self._last_status
        if self.servo_mode == "open_loop":
            if st and st["ptz"]["at_target"]:
                self._goto(State.ZOOM, "")
                return self._zoom_command()
            return []
        if self.servo.on_target():
            self._goto(State.ZOOM, "")
            return self._zoom_command()
        return self._aim_commands()

    def _zoom_command(self) -> list[Command]:
        d = self._last_det
        ptz = (d or {}).get("context", {}).get("ptz", {"pan_deg": 0.0, "tilt_deg": 0.0})
        z = float(np.clip(self.ctx.target_zoom if self.ctx else 3.0, 1.0, self.max_zoom))
        return [Command("PTZ_SET", {"pan_deg": float(np.clip(ptz.get("pan_deg", 0.0), -170, 170)),
                                    "tilt_deg": float(np.clip(ptz.get("tilt_deg", 0.0), -30, 60)),
                                    "zoom": z, "speed": "NORMAL"}, timeout_ms=3000)]

    def _st_zoom(self) -> list[Command]:
        st = self._last_status
        if st and st["ptz"]["at_target"] and st["ptz"]["focus_state"] == "LOCKED":
            self._goto(State.CAPTURE, "")
            return []
        if st and st["ptz"]["focus_state"] == "FAILED":
            return self._abort("DRIVER_ERROR", "对焦失败")
        return []

    def _st_capture(self) -> list[Command]:
        """A3：三种采集模式由 mission.capture.mode 决定。

        变焦到 3× 之后景深变浅，没对上焦的图送进二级模型只会浪费一次复核
        预算——所以 ZOOM 状态必须等到 focus = LOCKED 才进这里。
        """
        if self.capture_cb is None:
            self._goto(State.VERIFY, "")
            return []
        ok = self.capture_cb(self.ctx)
        if not ok:
            return self._abort("DRIVER_ERROR", "抓拍失败")
        self._goto(State.VERIFY, "")
        return []

    def _st_verify(self) -> list[Command]:
        d = self._last_det
        if d is not None and d.get("stage") == "VERIFY":
            det = self._pick_detection(d, self.ctx.track_id if self.ctx else None)
            if det is not None and self.ctx is not None:
                self.ctx.after = _snapshot(det, d["context"]["ptz"]["zoom"])
            self._goto(State.PACK, "")
        return []

    def _st_pack(self) -> list[Command]:
        if self.pack_cb is not None and self.ctx is not None:
            if not self.pack_cb(self.ctx):
                if self.ctx.abort is None:
                    self.ctx.abort = {"at_state": "PACK", "reason": "DRIVER_ERROR",
                                      "detail": "PACK_FAILED"}
        self._goto(State.RESUME, "")
        return self._home_and_resume()

    def _st_resume(self) -> list[Command]:
        st = self._last_status
        if st and st["chassis"]["state"] == "MOVING":
            self._finish_verify()
            self._goto(State.CRUISE, "")
            return []
        return []

    def _st_abort(self) -> list[Command]:
        st = self._last_status
        if st and st["chassis"]["state"] == "MOVING":
            self._finish_verify()
            self._goto(State.CRUISE, "")
            return []
        return []

    # ------------------------------------------------------------ 辅助
    def _finish_verify(self) -> None:
        if self.ctx is not None:
            self.suppress.on_verify_done(self.ctx.track_id, self.ctx.pose_xy)
            self.stats["verify_done"] += 1
        self.suppress.on_resume()
        self.ctx = None

    @staticmethod
    def _pick_detection(d: dict | None, track_id: int | None) -> dict | None:
        if not d or not d.get("detections"):
            return None
        if track_id is not None:
            for x in d["detections"]:
                if x.get("track_id") == track_id:
                    return x
        return d["detections"][0]

    def state_name(self) -> str:
        return self.state.value


def _snapshot(det: dict, zoom: float) -> dict:
    from patrol.common.messages import snapshot
    return snapshot(confidence=float(det.get("confidence", 0.0)),
                    pixel_density_px=float(det.get("pixel_density_px", 0.0)),
                    zoom=float(zoom),
                    est_distance_m=float(det.get("est_distance_m", 1.0)),
                    defect_class=det.get("defect_class"),
                    l2_reading=det.get("l2_reading"))
