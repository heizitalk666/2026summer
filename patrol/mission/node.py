#!/usr/bin/env python3
"""任务节点。ICD §1.1 / §7。

    python -m patrol.mission.node

mission 是复核状态机的宿主：订阅 IF-1（感知事件）与 IF-3（状态上报），
通过 IF-2 向网关发指令。它不直接碰执行器——所有指向底盘和云台的动作只有
gateway 一个出口，这是安全边界第一层的物理保证。

run_id 由 mission 在启动时生成，之后所有报文沿用（ICD §2.2）。
心跳 5 Hz 发，停发 1.5 s 网关就会介入。
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

from patrol.common import messages as M
from patrol.common.bus import RequestTimeout, Requester, Subscriber
from patrol.common.clock import mono_ns, stamps
from patrol.common.config import Config
from patrol.common.ids import SeqCounter, new_run_id, new_uuid
from patrol.common.logkit import build_logger, set_context
from patrol.gateway import limits as L
from patrol.mission.budget import build_budget
from patrol.mission.fsm import Command, MissionFSM, State
from patrol.mission.servo import GimbalServo
from patrol.mission.suppress import build_suppression


class MissionNode:
    def __init__(self, cfg: Config, *, capture_cb=None, pack_cb=None):
        self.cfg = cfg
        self.run_id = new_run_id()
        self.log = build_logger("mission", cfg, run_id=self.run_id)
        self.budget = build_budget(cfg)
        self.suppress = build_suppression(cfg)
        self.servo = GimbalServo(cfg)
        self.fsm = MissionFSM(cfg, budget=self.budget, suppress=self.suppress,
                              servo=self.servo, capture_cb=capture_cb,
                              pack_cb=pack_cb)
        self.fsm.on_transition = self._on_transition

        self.det_sub = Subscriber(cfg.get("bus.detection"), topics=["DETECTION_EVENT"])
        self.status_sub = Subscriber(cfg.get("bus.status"), topics=["STATUS_REPORT"])
        self.req = Requester(cfg.get("bus.command"), timeout_ms=2000)
        self.evidence_root = Path(cfg.get("uploader.evidence_dir", "evidence"))
        self.seq = SeqCounter()
        self._last_hb = 0.0
        self._cruise_pan: float | None = None
        self._running = False
        self.acks: list[dict] = []

    # ------------------------------------------------------------
    def _on_transition(self, prev: State, nxt: State, reason: str) -> None:
        self.log.info("状态转移 %s → %s" % (prev.value, nxt.value),
                      reason=reason or "-", budget=self.budget.remaining)
        if prev is State.AIM:
            m = self.servo.metrics().get("pan", {})
            self.log.info("AIM 结束", no_target=self.fsm.stats.get("aim_no_target", 0),
                          samples=m.get("samples", 0),
                          steady_px=m.get("steady_error_px"),
                          settling_s=m.get("settling_time_s"))
        if nxt in (State.PACK, State.ABORT):
            self._dump_ctx()
        if nxt is State.CRUISE:
            set_context(event_id=None)
        elif self.fsm.ctx is not None:
            set_context(event_id=self.fsm.ctx.event_id or None)

    def _dump_ctx(self) -> None:
        """把 FSM 这一侧的复核过程写进证据目录，供 uploader 合并进 manifest。

        **为什么不新开一条总线接口。**中止原因与状态耗时只有 FSM 知道，而
        uploader 只订阅 IF-1/IF-3，看不到状态机内部。ICD 冻结了四条接口，加第
        五条要走评审；而 §6.1 本来就把"证据目录的结构"定义成契约，两个进程对
        同一个 <run_id>/<event_id>/ 目录读写，是这个契约内的用法。

        不写这一步的后果实测过：manifest 里记的是 uploader 自己那条 60 s TTL
        （"超过 60 s 未收到 stage=VERIFY 的报文"），而真正的中止是 AIM 状态
        3 s 超时——评审看的就是这份 manifest，写错了等于把故障现场抹掉。
        """
        ctx = self.fsm.ctx
        if ctx is None or not ctx.event_id:
            return
        d = self.evidence_root / self.run_id / ctx.event_id
        payload = {
            "event_id": ctx.event_id,
            "track_id": ctx.track_id,
            "defect_class": ctx.defect_class,
            "waypoint_id": ctx.waypoint_id,
            "trigger_rule": ctx.trigger_rule,
            "target_zoom": round(float(ctx.target_zoom), 4),
            "used_aux": bool(ctx.used_aux),
            "pose_xy": list(ctx.pose_xy) if ctx.pose_xy else None,
            "timeline": ctx.timeline,
            "abort": ctx.abort,
            "final_state": self.fsm.state.value,
        }
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / "mission_ctx.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            self.log.warn("mission_ctx 落盘失败", detail=str(e))

    def _send(self, c: Command) -> dict | None:
        mono, utc = stamps()
        msg = M.build_command(
            cmd_id=new_uuid(), seq=self.seq.next(), ts_mono_ns=mono, ts_utc_ms=utc,
            run_id=self.run_id,
            event_id=(self.fsm.ctx.event_id if self.fsm.ctx else None),
            issued_by=c.issued_by, command=c.command, params=c.params,
            timeout_ms=c.timeout_ms,
            # PTZ_RATE 是 A1 的增补，尚未写入冻结 Schema，跳过发送前校验
            strict=(c.command != "PTZ_RATE"))
        try:
            ack = self.req.request(msg, timeout_ms=max(500, c.timeout_ms))
        except RequestTimeout as e:
            # 网关没回 ACK 会阻塞 mission，这是有意为之：宁可状态机卡在超时上
            # 被日志记下来，也不要指令悄悄丢失（ICD §4.4）
            self.log.warn("指令超时", command=c.command, detail=str(e))
            return None
        self.acks.append(ack)
        if ack.get("result") == "REJECTED":
            code = ack.get("reject_code")
            self.log.error("指令被拒", command=c.command, reject_code=code,
                           detail=(ack.get("reject_detail") or "")[:120])
            # 附录 A：只有 STATE_CONFLICT 与 DRIVER_TIMEOUT 允许重试，且只一次
            if code not in L.RETRYABLE and self.fsm.state not in (
                    State.CRUISE, State.ABORT):
                self.fsm._abort("DRIVER_ERROR", "指令被拒: %s" % code)
        return ack

    def _heartbeat(self) -> None:
        now = time.monotonic()
        if (now - self._last_hb) * 1000.0 < L.HEARTBEAT_PERIOD_MS:
            return
        self._last_hb = now
        self._send(Command("HEARTBEAT", {"mission_state": self.fsm.state_name()},
                           timeout_ms=1000))

    # ------------------------------------------------------------
    def step(self) -> None:
        """一拍。收报文 → 驱动状态机 → 发指令 → 发心跳。"""
        det = None
        for m in self.det_sub.drain(max_n=32):
            # 巡航报文只留最新一条（旧位姿没意义），但复核报文一次复核只有
            # 一条，被同批次的后续报文覆盖掉就再也拿不到了，必须单独收好
            self.fsm.note_verify(m)
            det = m
        st = None
        for m in self.status_sub.drain(max_n=64):
            # 安全事件不能被周期报文覆盖掉，必须立刻送进状态机
            if m.get("report_kind") == "SAFETY_EVENT":
                for c in self.fsm.tick(status=m):
                    self._send(c)
            st = m
        for c in self.fsm.tick(detection=det, status=st):
            self._send(c)
        # 巡航态才跟随车体转向；复核期间云台归状态机管，不能被抢走
        if st is not None and self.fsm.state is State.CRUISE:
            self.arm_cruise_ptz(yaw_deg=float(st["pose"]["yaw_deg"]))
        self._heartbeat()

    def cruise_pan_for(self, yaw_deg: float) -> float:
        """巡航云台的 pan：把 map 系的目标方位折算成相对车体的角度。

        pan = 目标方位 − 车体 yaw。柜列贴北墙（方位 90°），车往东走
        （yaw=0）时 pan=+90（左侧），折返往西走（yaw=180）时 pan=−90
        （右侧）——同一面墙，掉头后云台自动跟上。

        **不这么做会怎样**：把巡航姿态写死成"相对车体左转 90°"，车一折返
        云台就背对柜列，实测返程段检出直接归零，整段路白跑。
        """
        cp = self.cfg.get("mission.cruise_ptz")
        bearing = float(cp.get("look_map_bearing_deg", 90.0))
        pan = ((bearing - float(yaw_deg)) + 180.0) % 360.0 - 180.0
        if abs(pan) > 170.0:
            # 云台方位限位是 ±170°，转不到身后。车头朝向使柜列落在正后方
            # 附近时这是物理上做不到的事，夹紧后指向是错的——必须让它可见，
            # 而不是默默夹掉当没发生。本项目的过道是东西向，正常不会走到这里。
            self.log.warn("巡航指向超出云台限位，本段无法观测",
                          need_pan=round(pan, 1), yaw_deg=round(float(yaw_deg), 1))
        return float(max(-170.0, min(170.0, pan)))

    def arm_cruise_ptz(self, *, yaw_deg: float = 0.0, force: bool = False) -> None:
        """把云台摆到巡航姿态。开机调一次，之后车体转向时按需重发。

        **不做这一步系统等于闭着眼睛跑。**云台开机是 (0,0,1) 朝正前方，而
        柜面在侧方——实测默认姿态下 25 帧只有 1 个检出，摆正后有 47 个。
        """
        cp = self.cfg.get("mission.cruise_ptz")
        pan = self.cruise_pan_for(yaw_deg)
        if not force and self._cruise_pan is not None \
                and abs(pan - self._cruise_pan) < float(cp.get("retarget_deg", 12.0)):
            return
        self._cruise_pan = pan
        self._send(Command("PTZ_SET", {"pan_deg": pan,
                                       "tilt_deg": float(cp.get("tilt_deg", 0.0)),
                                       "zoom": float(cp.get("zoom", 1.0)),
                                       "speed": "NORMAL"}, timeout_ms=4000))
        self.log.info("云台巡航姿态", pan_deg=round(pan, 1), yaw_deg=round(yaw_deg, 1))

    def serve_forever(self) -> None:
        self._running = True
        self.arm_cruise_ptz(force=True)
        self.log.info("任务节点启动", run_id=self.run_id,
                      servo=self.cfg.get("mission.servo.mode"),
                      capture=self.cfg.get("mission.capture.mode"),
                      **self.budget.snapshot())
        period = float(self.cfg.get("mission.servo.period_ms", 100)) / 1000.0
        try:
            while self._running:
                t0 = time.monotonic()
                self.step()
                time.sleep(max(0.0, period - (time.monotonic() - t0)))
        finally:
            self.close()

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self._running = False
        self.det_sub.close()
        self.status_sub.close()
        self.req.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="任务节点")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    node = MissionNode(Config.load(a.config))
    signal.signal(signal.SIGINT, lambda *_: node.stop())
    signal.signal(signal.SIGTERM, lambda *_: node.stop())
    node.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
