"""底盘真机驱动：串口。docs/底盘串口协议.md v0.1。

    driver_mode: real   +   real.serial.chassis.port: /dev/ttyUSB0

硬件到位前可以先对着 `patrol/tools/fakecar.py` 调——那个假小车说同一套协议，
并复用 chassis_stub 的故障注入（停车延迟、丢包、安全事件、急停）。所以这里
的代码在硬件到货之前就能跑到，不是写完等着。

三点与桩对齐的语义（ICD §9.4 一致性要求）：

1. **所有会改变物理状态的方法非阻塞**：发完帧立刻返回 ExecHandle，完成判据
   由 status() 决定，不由本函数返回决定。
2. **STOPPING 与 STOPPED 必须区分**，HALT_REQ 等的是 STOPPED。
3. **安全事件走独立回调**，不等周期状态帧；从收到 SAF 到回调 ≤20 ms。

另有一条只在真机上存在：**保活**。协议约定上位机 ≥1 Hz 发 PNG，底盘 3 s
收不到就自行减速停车。这是底盘侧的独立保护，与 RK3576 上的网关看门狗是两
回事——网关看门狗管"AI 进程死了"，保活管"串口断了或整个 RK3576 死了"。
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from patrol.common.clock import mono_ns
from patrol.drivers.base import (ChassisCaps, ChassisState, ChassisStatus,
                                 ExecHandle, ExecProgress, ExecResult, IChassis,
                                 ParamOutOfRange)
from patrol.common.errors import DriverError, DriverNotReady
from patrol.drivers.real import serial_protocol as P
from patrol.drivers.real.serial_link import SerialLink


class _Job:
    __slots__ = ("seq", "kind", "t0_ns", "progress", "fail_reason")

    def __init__(self, seq: int, kind: str):
        self.seq, self.kind = seq, kind
        self.t0_ns = mono_ns()
        self.progress = ExecProgress.IN_PROGRESS
        self.fail_reason: str | None = None


class ChassisSerial(IChassis):
    def __init__(self, cfg, link: SerialLink | None = None):
        c = dict(cfg.get("real.serial.chassis", {}))
        self.cfg = cfg
        self.link = link or SerialLink(
            port=str(c.get("port", "/dev/ttyUSB0")),
            baudrate=int(c.get("baudrate", 115200)),
            read_timeout_s=float(c.get("read_timeout_s", 0.05)))
        self.ping_period_s = float(c.get("ping_period_s", 0.5))
        self.status_timeout_s = float(c.get("status_timeout_s", 1.0))
        self._caps = ChassisCaps(
            supports_task_level=bool(c.get("supports_task_level", True)),
            max_speed_mps=float(c.get("max_speed_mps", 1.0)),
            max_creep_m=float(c.get("max_creep_m", 0.5)),
            has_safety_layer=bool(c.get("has_safety_layer", True)),
            waypoint_ids=sorted(w["id"] for w in cfg.get("waypoints")),
        )

        self._lock = threading.RLock()
        self._seq = 0
        self._jobs: dict[str, _Job] = {}
        self._by_seq: dict[int, _Job] = {}
        self._safety_cbs: list[Callable[[dict], None]] = []
        self._status: dict | None = None
        self._status_ns = 0
        self._reader = P.LineReader()
        self._last_ping = 0.0
        self._stop = threading.Event()
        self._thr = threading.Thread(target=self._loop, name="chassis_serial",
                                     daemon=True)
        self._thr.start()

    # ------------------------------------------------------------ 收发
    def _next_seq(self) -> int:
        with self._lock:
            self._seq = (self._seq + 1) & 0xFFFF
            return self._seq

    def _send(self, kind: str, ftype: str, *payload) -> ExecHandle:
        seq = self._next_seq()
        h = ExecHandle("chassis-%s-%04x" % (kind.lower(), seq), mono_ns())
        job = _Job(seq, kind)
        with self._lock:
            self._jobs[h.handle_id] = job
            self._by_seq[seq] = job
        try:
            self.link.write(P.encode(ftype, seq, *payload))
        except OSError as e:
            job.progress = ExecProgress.FAILED
            job.fail_reason = "LINK_WRITE_FAILED"
            raise DriverError("底盘串口写失败: %s" % e) from e
        return h

    def _loop(self) -> None:
        while not self._stop.wait(0.005):
            try:
                chunk = self.link.read()
            except OSError:
                continue                     # 链路抖动：下一拍重试，别让线程死掉
            for line in self._reader.feed(chunk):
                try:
                    f = P.decode(line)
                except P.ProtocolError:
                    continue                 # 校验不过一律丢弃，不回执不猜
                self._on_frame(f)
            now = time.monotonic()
            if now - self._last_ping >= self.ping_period_s:
                self._last_ping = now
                try:
                    self.link.write(P.encode(P.CMD_PING, self._next_seq()))
                except OSError:
                    pass

    def _on_frame(self, f: P.Frame) -> None:
        if f.type == P.RSP_STATUS:
            st = P.parse_status(f)
            with self._lock:
                self._status, self._status_ns = st, mono_ns()
                self._settle_jobs(st["state"])
        elif f.type == P.RSP_ACK:
            self._on_ack(f)
        elif f.type == P.RSP_SAFETY:
            ev = P.parse_safety(f)
            ev["ts_mono_ns"] = mono_ns()
            for cb in list(self._safety_cbs):
                try:
                    cb(ev)                   # 回调内不得阻塞（ICD §8.1）
                except Exception:            # noqa: BLE001
                    pass

    def _on_ack(self, f: P.Frame) -> None:
        with self._lock:
            job = self._by_seq.get(f.int_field(0, -1))
        if job is None:
            return
        result = f.field(1, P.ACK_OK)
        if result == P.ACK_REJECT:
            job.progress = ExecProgress.FAILED
            job.fail_reason = f.field(2, "REJECTED")
        elif result == P.ACK_BUSY:
            job.progress = ExecProgress.PREEMPTED
            job.fail_reason = f.field(2, "BUSY")
        # ACK_OK 只表示"收到了"，动作是否完成由状态帧判定——这正是接口约定
        # 里"完成判据是 status()，不是本函数返回"的落点。

    def _settle_jobs(self, state: str) -> None:
        """状态帧到达时结算在途动作。调用方须持锁。"""
        for job in self._jobs.values():
            if job.progress is not ExecProgress.IN_PROGRESS:
                continue
            if state == "ESTOP" and job.kind != "RESUME":
                job.progress = ExecProgress.PREEMPTED
                job.fail_reason = "ESTOP_ACTIVE"
            elif job.kind in ("PAUSE", "CREEP_FORWARD", "GOTO_OBSERVE") \
                    and state == "STOPPED":
                job.progress = ExecProgress.DONE
            elif job.kind == "RESUME" and state in ("MOVING", "RETURNING"):
                job.progress = ExecProgress.DONE

    # ------------------------------------------------------------ IChassis
    def capabilities(self) -> ChassisCaps:
        return self._caps

    def pause(self, reason: str) -> ExecHandle:
        return self._send("PAUSE", P.CMD_PAUSE, str(reason)[:32])

    def resume(self) -> ExecHandle:
        return self._send("RESUME", P.CMD_RESUME)

    def creep_forward(self, distance_m: float) -> ExecHandle:
        d = float(distance_m)
        if not (0.0 < d <= self._caps.max_creep_m + 1e-9):
            # 驱动层按**硬件能力**校验，越界抛异常不截断（ICD §8.1 第二条）。
            # 底盘固件里还有独立的第二道限幅，两道都在，这是纵深防御不是冗余。
            raise ParamOutOfRange(
                "creep_forward %.3f m 超出硬件上限 %.3f m" % (d, self._caps.max_creep_m))
        return self._send("CREEP_FORWARD", P.CMD_CREEP, int(round(d * 1000)))

    def goto_observe(self, waypoint_id: str, tolerance_m: float) -> ExecHandle:
        if waypoint_id not in self._caps.waypoint_ids:
            raise ParamOutOfRange("巡检位 %r 不在底盘标定表内" % waypoint_id)
        return self._send("GOTO_OBSERVE", P.CMD_GOTO, waypoint_id,
                          int(round(float(tolerance_m) * 1000)))

    def status(self) -> ChassisStatus:
        with self._lock:
            st, age_ns = self._status, mono_ns() - self._status_ns
        if st is None:
            raise DriverNotReady("尚未收到底盘状态帧")
        if age_ns / 1e9 > self.status_timeout_s:
            # 状态过期不能当新鲜的用：上位机据此判断"车停稳了"，用过期数据
            # 会在车还在动的时候开始变焦抓拍。宁可报 FAULT 让状态机走超时。
            st = dict(st, state="FAULT", safety_layer_active=True)
        return ChassisStatus(
            state=ChassisState(st["state"]),
            speed_mps=st["speed_mps"], path_progress=st["path_progress"],
            distance_to_goal_m=st["distance_to_goal_m"],
            current_waypoint_id=st["current_waypoint_id"],
            battery_pct=st["battery_pct"],
            safety_layer_active=st["safety_layer_active"],
            ts_mono_ns=mono_ns())

    def poll(self, handle: ExecHandle) -> ExecResult:
        with self._lock:
            job = self._jobs.get(handle.handle_id)
        if job is None:
            return ExecResult(ExecProgress.FAILED, 0, "未知句柄")
        return ExecResult(job.progress,
                          int((mono_ns() - job.t0_ns) // 1_000_000), job.fail_reason)

    def subscribe_safety(self, cb: Callable[[dict], None]) -> None:
        self._safety_cbs.append(cb)

    def close(self) -> None:
        self._stop.set()
        self._thr.join(timeout=1.0)
        self.link.close()
