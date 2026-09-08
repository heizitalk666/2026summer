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

import math
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
    #: 触发那一刻算出的指向角（当前云台位姿 + aim_offset），body 系。
    #: AIM 进来时目标已经不在画面里的话，靠它把云台先摆回去重新找。
    aim_hint: tuple[float, float] | None = None
    used_aux: bool = False
    started_ns: int = field(default_factory=mono_ns)


def _center(det: dict) -> tuple[float, float]:
    b = det["bbox"]
    return (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0


class MissionFSM:
    """状态机本体。不碰 ZeroMQ，也不碰驱动——只吃事件、吐指令。

    这样它可以在测试里被逐状态驱动，不需要起四个进程。
    """

    #: AIM 期间允许的最大帧间形心跳变。云台上限 60 °/s，一拍 100 ms 最多扫过
    #: 6°，1920 px / 60° 下约 192 px；留一倍余量给检测框抖动。超过它的"同类
    #: 目标"必定是画面里的另一个东西。
    MAX_JUMP_PX = 400.0

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
        self.image_w = int(cfg.get("camera.width", 1920))
        self.image_h = int(cfg.get("camera.height", 1080))
        self.max_zoom = float(cfg.get("optics.max_zoom", 3.0))
        self.cruise_ptz = dict(cfg.get("mission.cruise_ptz"))

        self.state = State.CRUISE
        self.ctx: VerifyContext | None = None
        self._deadline: Deadline | None = None
        self._entered_ns = mono_ns()
        self._confirm = 0
        self._confirm_track: int | None = None
        self._ff_done = False           # AIM 的前馈那一步是否已经走完
        self._ff_moved = False
        self._aim_last_cxy: tuple[float, float] | None = None
        self._resume_retried = False
        self._last_status: dict | None = None
        self._last_det: dict | None = None
        self._last_verify: dict | None = None
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
        if nxt is State.HALT_REQ:
            # 新一次复核开始，丢掉上一轮遗留的复核报文，免得拿旧的当新的用
            self._last_verify = None
        if nxt is State.AIM:
            self.servo.reset()
            self._ff_done = False
            self._ff_moved = False
            self._aim_last_cxy = None
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
        """ABORT 与 RESUME 的出口动作完全一致：云台归位 + 恢复巡检。

        归位目标是**巡航姿态**而不是 (0,0,1)：回到正前方等于让云台背对柜列，
        下一段路就白跑了。pan 由当前车体 yaw 实时折算，见 node.cruise_pan_for。
        """
        yaw = 0.0
        if self._last_status is not None:
            yaw = float(self._last_status.get("pose", {}).get("yaw_deg", 0.0))
        bearing = float(self.cruise_ptz.get("look_map_bearing_deg", 90.0))
        pan = ((bearing - yaw) + 180.0) % 360.0 - 180.0
        return [
            Command("PTZ_SET", {"pan_deg": float(max(-170.0, min(170.0, pan))),
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
            self.note_verify(detection)

        if self._deadline is not None and self._deadline.expired():
            return self._on_timeout()

        handler = getattr(self, "_st_" + self.state.value.lower())
        return handler() or []

    def note_verify(self, ev: dict | None) -> None:
        """记下一条 stage=VERIFY 的报文，等状态机走到 VERIFY 时再取用。

        **这是一条真实存在的竞态。**perception 判断"该复核了"靠的是 IF-3 的
        状态组合（停稳 + 变焦到位 + 对焦锁定，见 perception.node.verify_due），
        这个条件在 mission 还处于 ZOOM 或 CAPTURE 时就已经成立了。于是复核
        报文可能比状态机早到一两拍；而 node.step() 每拍只保留最后一条 IF-1，
        随后的巡航报文会把它覆盖掉，等状态机真正进到 VERIFY 时已经没了，只能
        干等 5 s 超时 ABORT。实测九个证据包里有三个是这么废掉的。

        单独存一份而不是改 _last_det 的覆盖策略，是因为巡航报文本来就该只保
        留最新的一条——旧的位姿没有意义。VERIFY 报文不一样，它一次复核只有
        一条，丢了就没了。
        """
        if ev is not None and ev.get("stage") == "VERIFY":
            self._last_verify = ev

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
        # **云台正在扫转时不接受触发。**车折返时巡航指向要从 +90° 摆到 -90°，
        # 180° 摆幅按 60°/s 要 3 秒；这期间目标从画面里一闪而过，据此发起的
        # 复核等进到 AIM 时目标早已扫走，只能干等到超时再 ABORT。实测这一条
        # 是"复核全部失败"的直接原因：AIM 期间 30 拍拿不到目标、伺服一个样本
        # 都没采到。扫转中的帧本身也有运动模糊，不该拿来定案。
        st = self._last_status
        if st is not None and (st["ptz"]["moving"] or not st["ptz"]["at_target"]):
            self._confirm = 0
            self._confirm_track = None
            return []
        if d is None or not d.get("suspect", {}).get("is_suspect"):
            self._confirm = 0
            self._confirm_track = None
            return []
        # **三帧必须是同一条 track。**只数帧数的话，一帧误检加两帧真表计也能
        # 凑够三帧，然后按误检那条 track 去 AIM——目标根本不存在，伺服采不到
        # 样本，只能等超时 ABORT，一次复核预算就这么废了。
        tid = d["suspect"].get("target_track_id")
        if tid != self._confirm_track:
            self._confirm_track = tid
            self._confirm = 0
        self._confirm += 1
        if self._confirm < self.confirm_frames:
            return []          # 连续 N 帧确认，挡住单帧噪声
        self._confirm = 0
        self._confirm_track = None
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
            # 记下触发那一刻的指向角。停车要 1.5–2.5 s，这期间车还要往前滑
            # 一米左右，画面边缘的目标可能已经划出去了；进到 AIM 才发现没有
            # 反馈量可用，就只能干等超时。有了它至少能先把云台摆回目标当时
            # 所在的方位，给检测器一次重新找到它的机会。
            ptz0, off = d["context"]["ptz"], det.get("aim_offset", {})
            self.ctx.aim_hint = (
                float(np.clip(ptz0["pan_deg"] + off.get("pan_deg", 0.0), -170, 170)),
                float(np.clip(ptz0["tilt_deg"] + off.get("tilt_deg", 0.0), -30, 60)))
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

    def _feedforward_command(self, d: dict, det: dict) -> Command:
        """把 aim_offset 一次性加到当前云台位姿上。

        aim_offset 由针孔几何直接算出（optics.aim_offset_deg），是**前馈**量。
        open_loop 模式就到此为止；pid 模式拿它做初值，剩下的残差交给闭环。
        """
        ptz = d["context"]["ptz"]
        off = det.get("aim_offset", {})
        return Command("PTZ_SET", {
            "pan_deg": float(np.clip(ptz["pan_deg"] + off.get("pan_deg", 0.0), -170, 170)),
            "tilt_deg": float(np.clip(ptz["tilt_deg"] + off.get("tilt_deg", 0.0), -30, 60)),
            "zoom": 1.0, "speed": "NORMAL"}, timeout_ms=3000)

    def _reacquire(self, d: dict | None) -> dict | None:
        """AIM 期间取反馈量：先认 track_id，跟丢了按"同类别 + 离画面中心最近"重认。

        **为什么不能只认 track_id。**IoU 跟踪靠帧间框重叠匹配，而 AIM 的前馈
        是一次二十几度的甩头，一帧之内框能平移一百五十像素以上，重叠归零，
        跟踪必然断链并分配新 id。实测就是这样：前馈把目标端端正正送到画面中心
        （cx 从 1638 收到 985），可按 id 一个都对不上，PID 一个样本没采到，
        AIM 只能干等到 3 s 超时。**真机上云台一动同样会断链，这不是桩的毛病。**

        **但也不能退化到 detections[0]。**那会把画面里另一个目标的形心喂进
        闭环：实测一帧远处压力表的框（cx=1940）就往回路里灌了 −980 px 的假
        阶跃，云台猛地甩出去，白白吃掉一秒整定时间。

        折中是按类别重认，并在同类中取离画面中心最近的那个——刚做完指向，
        要找的目标本来就该在中心附近。重认成功后把新 id 写回 ctx，后续的
        同目标冷却与 before/after 配对才跟得上。
        """
        dets = (d or {}).get("detections") or []
        if not dets:
            return None
        tid = self.ctx.track_id if self.ctx else None
        if tid is not None:
            for x in dets:
                if x.get("track_id") == tid:
                    return x
        want = self.ctx.defect_class if self.ctx else None
        same = [x for x in dets if x.get("defect_class") == want] if want else []
        if not same:
            return None
        cx0, cy0 = self.image_w / 2.0, self.image_h / 2.0
        best = min(same, key=lambda x: (
            ((x["bbox"][0] + x["bbox"][2]) / 2.0 - cx0) ** 2
            + ((x["bbox"][1] + x["bbox"][3]) / 2.0 - cy0) ** 2))
        if self.ctx is not None:
            self.ctx.track_id = best.get("track_id")
        return best

    def _aim_commands(self, *, first: bool = False) -> list[Command]:
        """AIM：先在广角端把目标转到画面中心，再变焦。"""
        d = self._last_det
        det = self._reacquire(d)
        if det is None:
            # AIM 期间丢了目标是个要能看见的事件：伺服没有反馈量可用，
            # 只能干等到超时。计数暴露出来，便于判断是"目标出框"还是
            # "检测器漏检"。
            self.stats["aim_no_target"] = self.stats.get("aim_no_target", 0) + 1
            if first and self.ctx is not None and self.ctx.aim_hint is not None:
                # 一进 AIM 就没有目标，多半是停车那 1.5–2.5 s 里车又往前滑了，
                # 画面边缘的目标划出去了。先按触发时记下的方位把云台摆回去，
                # 给检测器一次重新找到它的机会——总比原地干等三秒强。
                pan, tilt = self.ctx.aim_hint
                return [Command("PTZ_SET", {"pan_deg": pan, "tilt_deg": tilt,
                                            "zoom": 1.0, "speed": "NORMAL"},
                                timeout_ms=3000)]
            return []
        if self.servo_mode == "open_loop":
            # ICD v1.0 原样：一次 PTZ_SET + 等 at_target，没有后续修正
            return [self._feedforward_command(d, det)]
        if first:
            # **A1 推荐通路的第一步也是前馈，不是直接进速率闭环。**
            #
            # 纯速率闭环在这套系统里有个绕不过去的物理限制：感知 10 Hz、任务
            # 10 Hz，加上渲染与总线往返，反馈至少滞后 200 ms；云台上限 60 °/s，
            # 这 200 ms 里已经扫过 12°。所以哪怕 PID 整定得再好，一次 22° 的
            # 大角度指向也必然冲过头十几度再荡回来——实测 AIM 要 2.2 s，把
            # 3.0 s 的预算吃掉七成，稍有负载就超时 ABORT。
            #
            # 前馈把这段几何上完全已知的角度一次给足（aim_offset 是针孔投影
            # 直接解出来的，不含未知量），云台按自己的加减速曲线走位置环，
            # 不受反馈滞后影响；PID 只负责收掉检测框抖动带来的几十像素残差。
            # 这正是方案书对 aim_offset 的定位——"前馈量，PID 用它做初值"。
            return [self._feedforward_command(d, det)]
        # 残差修正：速率闭环
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
        # 先等前馈那一步走完再开闭环。两者同时下发会打架：PTZ_RATE 会把
        # set_pose 的目标清掉，前馈等于白发。
        if not self._ff_done:
            if st is None:
                return []
            if st["ptz"]["moving"] or not st["ptz"]["at_target"]:
                self._ff_moved = True
            elif self._ff_moved or (mono_ns() - self._entered_ns) > 500_000_000:
                # 要么已经动过并停稳，要么前馈量小到云台压根没离开到位带
                self._ff_done = True
            return []
        if self.servo.on_target():
            self._goto(State.ZOOM, "")
            return self._zoom_command()
        return self._aim_commands()

    def _same_event(self, d: dict) -> bool:
        """VERIFY 报文的 event_id 必须与本次复核一致。

        event_id 是串起四份 Schema 的主键（ICD §2.2）。收下一条属于**别的**
        事件的 VERIFY 报文，会让证据包的 before/after 分属两个目标，增益指标
        随之失去意义。宁可让本次复核超时 ABORT——那是看得见的失败。
        报文没带 event_id 时放行，那是感知侧的兼容路径，不是错配。
        """
        eid = d.get("event_id")
        return not eid or not (self.ctx and self.ctx.event_id) \
            or eid == self.ctx.event_id

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
        """抓拍。变焦到 3× 之后景深变浅，没对上焦的图送进二级模型只会浪费一次
        复核预算——所以 ZOOM 状态必须等到 focus = LOCKED 才进这里。

        **A3 的三种采集模式（conditional / burst / multiview）目前没有实现。**
        本状态把抓拍委托给 ``capture_cb``，而 ``patrol/mission/node.py`` 构造
        ``MissionNode`` 时不传这个回调，所以运行时这里直接转 VERIFY，图像由
        perception 侧自己抓。``self.capture_mode`` / ``self.highlight_trigger`` /
        ``VerifyContext.aux_frames`` / ``used_aux`` 因此都是只赋值不读取的死字段，
        ``multiview_spread`` 也没有任何代码产出。

        接口侧已经冻结（ICD v2.0 §7.2 的条件路径、``files[].role`` 的
        ``VERIFY_FRAME_AUX``、``snapshot.multiview_spread``），**实现侧欠着**。
        补的时候要跨进程：mission 判定 → 经网关下发 ±15° 的 PTZ_SET → perception
        抓帧 → 回传读数算极差。留着这些字段是为了那天不用再动接口。
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
        d = self._last_verify
        if d is not None and self._same_event(d):
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
    def _pick_detection(d: dict | None, track_id: int | None,
                        *, strict: bool = False) -> dict | None:
        """按 track_id 取检出。

        ``strict`` 时找不到就返回 None，不退化到 detections[0]。伺服闭环必须
        用 strict——喂错目标比没有反馈量更糟。VERIFY 那一支则不能 strict：
        变焦之后跟踪可能重新分配 id，退化到第一个检出才是对的。
        """
        if not d or not d.get("detections"):
            return None
        if track_id is not None:
            for x in d["detections"]:
                if x.get("track_id") == track_id:
                    return x
            if strict:
                return None
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
