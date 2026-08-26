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
import signal
import sys
import time

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
        self.seq = SeqCounter()
        self._last_hb = 0.0
        self._running = False
        self.acks: list[dict] = []

    # ------------------------------------------------------------
    def _on_transition(self, prev: State, nxt: State, reason: str) -> None:
        self.log.info("状态转移 %s → %s" % (prev.value, nxt.value),
                      reason=reason or "-", budget=self.budget.remaining)
        if nxt is State.CRUISE:
            set_context(event_id=None)
        elif self.fsm.ctx is not None:
            set_context(event_id=self.fsm.ctx.event_id or None)

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
        self._heartbeat()

    def serve_forever(self) -> None:
        self._running = True
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
