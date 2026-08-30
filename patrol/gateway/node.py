#!/usr/bin/env python3
"""安全网关。ICD §1.1 / §4 / §5。

    python -m patrol.gateway.node

**系统里唯一能让车动、让云台动的通路。**安全边界的第一层和第二层都落在这里。

三层安全机制（方案书 §7.4），三级独立生效，任一级单独失效不导致边界被突破：

| 层级     | 限制什么                 | 落在哪                                    |
|----------|--------------------------|-------------------------------------------|
| 协议层   | 识别模块能表达什么       | limits.WHITELIST + Schema 的 additionalProperties |
| 网关层   | 表达的内容是否被接受     | checks.py 的五项校验，范围值硬编码        |
| 优先级层 | 被接受的指令是否被执行   | 底盘安全层，独立于网关运行，可否决任何上层指令 |

网关做四件事：

1. 收 IF-2 指令 → 五项校验 → 派发到驱动 → 回 ACK（REQ/REP 强制每条必有回执）
2. 20 Hz 发 IF-3 StatusReport，安全事件不等周期立刻插播
3. 心跳看门狗：1500 ms 没心跳则自行下发 RESUME 让车走完路线
4. 审计日志：每条指令的校验结果逐项落盘，评审时抽查
"""
from __future__ import annotations

import argparse
import queue
import signal
import sys
import time

from patrol.common import messages as M
from patrol.common.bus import Publisher, Replier
from patrol.common.clock import mono_ns, stamps
from patrol.common.config import Config
from patrol.common.errors import (DriverError, DriverNotReady, DriverTimeout,
                                  ParamOutOfRange)
from patrol.common.ids import SeqCounter, new_uuid
from patrol.common.logkit import JsonlSink, build_logger
from patrol.drivers.base import (ExecHandle, ExecProgress, IChassis, ICamera,
                                 ILocalizer, IPTZ, PTZSpeed, selftest)
from patrol.drivers.factory import build_drivers
from patrol.gateway import checks as CK
from patrol.gateway import limits as L
from patrol.gateway.watchdog import Watchdog

_DRIVER_ERR_CODE = {
    DriverNotReady: "DRIVER_NOT_READY",
    DriverTimeout: "DRIVER_TIMEOUT",
    ParamOutOfRange: "PARAM_OUT_OF_RANGE",
}


class GatewayNode:
    def __init__(self, cfg: Config, *, seed: int = 0,
                 drivers: tuple[IChassis, IPTZ, ICamera, ILocalizer] | None = None):
        self.cfg = cfg
        self.log = build_logger("gateway", cfg)
        self.audit = JsonlSink(cfg.get("gateway.audit_log", "logs/gateway-audit.jsonl"))

        self.chassis, self.ptz, self.camera, self.loc = (
            drivers if drivers is not None else build_drivers(cfg, seed=seed))
        problems = selftest(self.chassis, self.ptz, self.camera)
        if problems:
            # capabilities() 是开机自检的依据：不满足则拒绝启动并打印差在哪，
            # 而不是等到现场发现表读不出来（ICD §8.1 第三条）
            for p in problems:
                self.log.critical("开机自检未通过", detail=p)
            raise SystemExit("开机自检未通过，网关拒绝启动")

        self.allow_rate = bool(cfg.get("gateway.enable_ptz_rate", True))
        caps = self.chassis.capabilities()
        # 网关启动时加载标定表；GOTO_OBSERVE 的 waypoint 必须在表内
        self.waypoints = frozenset(caps.waypoint_ids)

        self.rep = Replier(cfg.get("bus.command"))
        self.pub = Publisher(cfg.get("bus.status"))
        self.seq = SeqCounter()
        self.watchdog = Watchdog()
        self.run_id = "00000000-000000-0000"

        self._safety_q: "queue.Queue[dict]" = queue.Queue()
        self.chassis.subscribe_safety(self._on_safety)
        self._pending: dict[str, tuple[str, object, str | None]] = {}   # handle -> (cmd_id, driver, event_id)
        self._status_period = 1.0 / float(cfg.get("gateway.status_rate_hz", 20))
        self._next_status = 0.0
        self._running = False
        self._last_rate_ns = 0

    # ------------------------------------------------------------ 安全事件
    def _on_safety(self, ev: dict) -> None:
        """驱动内部线程触发。ZeroMQ 套接字非线程安全，只入队，主循环发。

        主循环 poll 超时 5 ms，所以从事件发生到 SAFETY_EVENT 发出通常 <10 ms，
        留给 mission 的 200 ms 中止预算绰绰有余。
        """
        self._safety_q.put(ev)

    # ------------------------------------------------------------ 指令处理
    def handle_command(self, cmd: dict) -> dict:
        t0 = mono_ns()
        cmd_id = cmd.get("cmd_id") or new_uuid()
        ch = M.all_checks("SKIP")
        st = self.chassis.status()

        # 心跳超时期间拒绝 MISSION_FSM 的指令（ICD §4.5），**但心跳本身除外**。
        #
        # ICD §4.5 里两句话是自相矛盾的：「心跳超时期间，网关拒绝一切
        # issued_by = MISSION_FSM 的指令」与「恢复条件：心跳恢复且连续 3 条
        # 正常，网关解除看门狗态」。心跳本身就是 MISSION_FSM 发的，按前一句
        # 拒掉之后后一句永远不可能满足——看门狗一旦触发就死锁，AI 进程重启
        # 回来也接管不了车。实测确实如此。
        #
        # 按意图解释：要拒的是**动作指令**，心跳是恢复通道，必须放行。
        # 这条已记入差异清单待评审，建议 ICD §4.5 补一句"HEARTBEAT 除外"。
        if (self.watchdog.triggered and cmd.get("issued_by") == "MISSION_FSM"
                and cmd.get("command") != "HEARTBEAT"):
            return self._reject(cmd_id, ch, "HEARTBEAT_LOST",
                                "看门狗已介入，拒绝 MISSION_FSM 的动作指令", cmd, t0)

        steps = (
            ("whitelist", lambda: CK.check_whitelist(cmd, allow_ptz_rate=self.allow_rate)),
            ("schema", lambda: CK.check_schema(cmd)),
            ("range", lambda: CK.check_range(cmd, known_waypoints=self.waypoints)),
            ("state_conflict", lambda: CK.check_state_conflict(cmd, st.state.value)),
            ("safety_override", lambda: CK.check_safety_override(
                cmd, safety_active=st.safety_layer_active)),
        )
        for name, fn in steps:
            ok, code, detail = fn()
            ch[name] = "PASS" if ok else "FAIL"
            if not ok:
                return self._reject(cmd_id, ch, code, detail, cmd, t0)

        # 心跳只更新看门狗，不碰执行器
        if cmd["command"] == "HEARTBEAT":
            if self.watchdog.on_heartbeat():
                self.log.info("心跳恢复，看门狗解除")
            self.run_id = cmd.get("run_id", self.run_id)
            return self._accept(cmd_id, ch, "heartbeat", cmd, t0)

        try:
            handle = self._dispatch(cmd)
        except tuple(_DRIVER_ERR_CODE) as e:
            code = _DRIVER_ERR_CODE[type(e)]
            ch["range"] = "FAIL" if code == "PARAM_OUT_OF_RANGE" else ch["range"]
            return self._reject(cmd_id, ch, code, str(e), cmd, t0)
        except DriverError as e:
            return self._reject(cmd_id, ch, "DRIVER_NOT_READY", str(e), cmd, t0)

        self._pending[handle.handle_id] = (
            cmd_id, self.ptz if cmd["command"].startswith("PTZ") else self.chassis,
            cmd.get("event_id"))
        return self._accept(cmd_id, ch, handle.handle_id, cmd, t0)

    def _dispatch(self, cmd: dict) -> ExecHandle:
        c, p = cmd["command"], cmd.get("params") or {}
        if c == "PAUSE":
            return self.chassis.pause(p["reason"])
        if c == "RESUME":
            return self.chassis.resume()
        if c == "CREEP_FORWARD":
            return self.chassis.creep_forward(float(p["distance_m"]))
        if c == "GOTO_OBSERVE":
            return self.chassis.goto_observe(p["waypoint_id"], float(p["tolerance_m"]))
        if c == "PTZ_SET":
            return self.ptz.set_pose(float(p["pan_deg"]), float(p["tilt_deg"]),
                                     float(p["zoom"]), PTZSpeed(p["speed"]))
        if c == "PTZ_RATE":
            self._last_rate_ns = mono_ns()
            return self.ptz.set_rate(float(p["pan_dps"]), float(p["tilt_dps"]),
                                     int(p["ttl_ms"]))
        raise DriverError("白名单已通过但没有派发分支: %s" % c)

    # ------------------------------------------------------------ ACK
    def _accept(self, cmd_id, ch, handle_id, cmd, t0) -> dict:
        ack = M.build_ack(cmd_id=cmd_id, ts_mono_ns=mono_ns(), result="ACCEPTED",
                          checks=ch, exec_handle=handle_id)
        self._audit(cmd, ack, t0)
        return ack

    def _reject(self, cmd_id, ch, code, detail, cmd, t0) -> dict:
        ack = M.build_ack(cmd_id=cmd_id, ts_mono_ns=mono_ns(), result="REJECTED",
                          checks=ch, reject_code=code, reject_detail=detail)
        self.log.warn("指令被拒", command=cmd.get("command"), reject_code=code,
                      detail=(detail or "")[:120])
        self._audit(cmd, ack, t0)
        # 协议外指令要上报 SafetyEvent（ICD 附录 B.3 ILLEGAL_COMMAND）
        if code in ("NOT_IN_WHITELIST", "SCHEMA_INVALID"):
            self._publish_status("SAFETY_EVENT", safety={
                "event_type": "ILLEGAL_COMMAND", "severity": "WARN", "source": "GATEWAY",
                "action_taken": "NONE", "brake_latency_ms": None,
                "detail": ("%s: %s" % (code, detail or ""))[:256]})
        return ack

    def _audit(self, cmd: dict, ack: dict, t0: int) -> None:
        self.audit.write({
            "ts_utc_ms": stamps()[1], "cmd_id": ack.get("cmd_id"),
            "event_id": cmd.get("event_id"), "run_id": cmd.get("run_id"),
            "issued_by": cmd.get("issued_by"), "command": cmd.get("command"),
            "params": cmd.get("params"), "result": ack.get("result"),
            "reject_code": ack.get("reject_code"),
            "reject_detail": ack.get("reject_detail"),
            "checks": ack.get("checks"), "exec_handle": ack.get("exec_handle"),
            "handle_us": int((mono_ns() - t0) // 1000),
        })

    # ------------------------------------------------------------ 上报
    def _snapshot(self) -> tuple[dict, dict, dict, dict]:
        cs, ps, po = self.chassis.status(), self.ptz.status(), self.loc.get_pose()
        chassis = {
            "state": cs.state.value, "speed_mps": max(0.0, min(1.5, cs.speed_mps)),
            "path_progress": max(0.0, min(1.0, cs.path_progress)),
            "distance_to_goal_m": (None if cs.distance_to_goal_m is None
                                   else max(0.0, round(cs.distance_to_goal_m, 3))),
            "current_waypoint_id": cs.current_waypoint_id,
            "battery_pct": max(0.0, min(100.0, cs.battery_pct)),
            "safety_layer_active": bool(cs.safety_layer_active),
        }
        ptz = {
            "pan_deg": max(-170.0, min(170.0, ps.pan_deg)),
            "tilt_deg": max(-30.0, min(60.0, ps.tilt_deg)),
            "zoom": max(1.0, min(3.0, ps.zoom)), "hfov_deg": ps.hfov_deg,
            "moving": bool(ps.moving), "focus_state": ps.focus_state.value,
            "at_target": bool(ps.at_target),
        }
        pose = {
            "x_m": po.x_m, "y_m": po.y_m,
            "yaw_deg": max(-180.0, min(180.0, po.yaw_deg)),
            "cov_trace": max(0.0, po.cov_trace), "valid": bool(po.valid),
            "source": po.source.value,
        }
        return chassis, ptz, pose, self.watchdog.snapshot()

    def _publish_status(self, kind: str, *, exec_: dict | None = None,
                        safety: dict | None = None) -> None:
        chassis, ptz, pose, wd = self._snapshot()
        mono, utc = stamps()
        try:
            msg = M.build_status_report(
                seq=self.seq.next(), ts_mono_ns=mono, ts_utc_ms=utc,
                run_id=self.run_id, report_kind=kind, chassis=chassis, ptz=ptz,
                pose=pose, watchdog=wd, exec_=exec_, safety=safety)
        except M.SchemaViolation as e:
            self.log.error("StatusReport 自身不合法，丢弃", detail=str(e)[:160])
            return
        self.pub.send(msg)

    def _poll_exec(self) -> None:
        for hid, (cmd_id, drv, event_id) in list(self._pending.items()):
            res = drv.poll(ExecHandle(hid, mono_ns()))
            if res.progress is ExecProgress.IN_PROGRESS:
                continue
            self._pending.pop(hid, None)
            self._publish_status("EXEC_UPDATE", exec_={
                "exec_handle": hid, "cmd_id": cmd_id, "progress": res.progress.value,
                "elapsed_ms": max(0, res.elapsed_ms), "fail_reason": res.fail_reason})

    # ------------------------------------------------------------ 主循环
    def serve_forever(self) -> None:
        self._running = True
        self.log.info("网关启动", command=self.cfg.get("bus.command"),
                      status=self.cfg.get("bus.status"),
                      waypoints=len(self.waypoints), ptz_rate=self.allow_rate)
        while self._running:
            self.rep.serve_once(self.handle_command, timeout_ms=5)

            # 安全事件插播，不等周期
            while not self._safety_q.empty():
                ev = self._safety_q.get_nowait()
                self.log.warn("安全事件", event_type=ev["event_type"],
                              brake_latency_ms=ev.get("brake_latency_ms"))
                self._publish_status("SAFETY_EVENT", safety={
                    "event_type": ev["event_type"], "severity": ev.get("severity", "CRITICAL"),
                    "source": ev.get("source", "CHASSIS_SAFETY_LAYER"),
                    "action_taken": ev.get("action_taken", "BRAKE"),
                    "brake_latency_ms": ev.get("brake_latency_ms"),
                    "detail": str(ev.get("detail", ""))[:256]})

            if self.watchdog.check():
                # 看门狗介入：让车**继续走完巡检路线**，不是让车停住。
                # AI 崩了时车停在通道中间比走完路线回充电位更麻烦。
                self.log.critical("心跳丢失，看门狗介入，自行下发 RESUME",
                                  age_ms=self.watchdog.age_ms())
                try:
                    self.chassis.resume()
                except DriverError as e:
                    self.log.error("看门狗 RESUME 失败", detail=str(e))
                self._publish_status("SAFETY_EVENT", safety={
                    "event_type": "HEARTBEAT_LOST", "severity": "WARN", "source": "GATEWAY",
                    "action_taken": "FORCE_RESUME", "brake_latency_ms": None,
                    "detail": "心跳超过 %d ms 未到，网关下发 RESUME" % L.HEARTBEAT_TIMEOUT_MS})

            self._poll_exec()

            now = time.monotonic()
            if now >= self._next_status:
                self._next_status = now + self._status_period
                self._publish_status("PERIODIC")

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self.stop()
        for d in (self.camera, self.ptz, self.chassis, self.loc):
            try:
                d.close()
            except Exception:                # noqa: BLE001
                pass
        self.rep.close()
        self.pub.close()
        self.audit.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="安全网关")
    ap.add_argument("--config", default=None)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    node = GatewayNode(Config.load(a.config), seed=a.seed)
    signal.signal(signal.SIGINT, lambda *_: node.stop())
    signal.signal(signal.SIGTERM, lambda *_: node.stop())
    try:
        node.serve_forever()
    finally:
        node.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
