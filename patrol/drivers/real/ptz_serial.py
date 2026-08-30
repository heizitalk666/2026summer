"""云台真机驱动：串口。

**协议是本项目自定的一版草案（PTZ v0.1），不是底盘那份。**云台多半会随
整机附一套厂商协议（Pelco-D、VISCA 或私有），到货后只需要把 `_encode_*`
这几个方法换掉，上面的语义层不用动。这里先把语义层定下来，理由和底盘一样：
先定协议、配仿真，硬件到货只改端口名。

沿用底盘那套帧格式（`$TYPE,seq,...*CRC8`），因为编解码是现成的、可以直接
`cat /dev/ttyUSB1` 看，而且两条链路的调试手法一致。

    上位机 → 云台        云台 → 上位机
    SET pan,tilt,zoom,speed   PST pan_mdeg,tilt_mdeg,zoom_x100,moving,focus,at_target
    RAT pan_mdps,tilt_mdps,ttl_ms
    HOM （归位到 0,0,1×）

角度用**毫度**、变焦用**百分之一倍**传，与底盘协议一样全整数，避免浮点
格式在两端对不上。

一条与桩一致的语义：`set_rate` 的 `ttl_ms` 是安全兜底，**归零由云台固件
自己执行**，不依赖上位机。上位机崩了云台必须自己停下来，而不是一直转到限位。
"""
from __future__ import annotations

import math
import threading

from patrol.common.clock import mono_ns
from patrol.common.errors import DriverNotReady
from patrol.drivers.base import (ExecHandle, ExecProgress, ExecResult,
                                 FocusState, IPTZ, ParamOutOfRange, PTZCaps,
                                 PTZSpeed, PTZStatus)
from patrol.drivers.real import serial_protocol as P
from patrol.drivers.real.serial_link import SerialLink, open_link
from patrol.scene.optics import hfov_at_zoom

CMD_SET = "SET"
CMD_RATE = "RAT"
CMD_HOME = "HOM"
RSP_POSE = "PST"

_FOCUS = {"F": FocusState.FOCUSING, "L": FocusState.LOCKED, "X": FocusState.FAILED}


class PTZSerial(IPTZ):
    def __init__(self, cfg, link: SerialLink | None = None):
        c = dict(cfg.get("real.serial.ptz", {}))
        # 走 open_link 而不是直接 SerialLink：端口写成 tcp://host:port
        # 时自动切到 TCP 环回，用于 Windows 上的假小车联调（PTY 不可用）。
        self.link = link or open_link(
            port=str(c.get("port", "/dev/ttyUSB1")),
            baudrate=int(c.get("baudrate", 115200)),
            read_timeout_s=float(c.get("read_timeout_s", 0.05)))
        self._caps = PTZCaps(
            pan_range_deg=tuple(c.get("pan_range_deg", [-170.0, 170.0])),
            tilt_range_deg=tuple(c.get("tilt_range_deg", [-30.0, 60.0])),
            max_zoom=float(c.get("max_zoom", 3.0)),
            hfov_at_1x_deg=float(cfg.get("optics.hfov_at_1x_deg", 60.0)),
            zoom_is_optical=bool(c.get("zoom_is_optical", True)),
            max_pan_dps=float(c.get("max_pan_dps", 60.0)),
            max_tilt_dps=float(c.get("max_tilt_dps", 40.0)))
        self.status_timeout_s = float(c.get("status_timeout_s", 1.0))

        self._lock = threading.RLock()
        self._seq = 0
        # handle -> (seq, kind, t0_ns, target)；target 是 (pan, tilt, zoom) 或 None
        self._jobs: dict[str, tuple[int, str, int, tuple | None]] = {}
        self._done: dict[str, ExecResult] = {}
        self._pose: dict | None = None
        self._pose_ns = 0
        self._reader = P.LineReader()
        self._stop = threading.Event()
        self._thr = threading.Thread(target=self._loop, name="ptz_serial", daemon=True)
        self._thr.start()

    # ------------------------------------------------------------
    def _next_seq(self) -> int:
        with self._lock:
            self._seq = (self._seq + 1) & 0xFFFF
            return self._seq

    def _send(self, kind: str, ftype: str, *payload,
              target: tuple | None = None) -> ExecHandle:
        seq = self._next_seq()
        h = ExecHandle("ptz-%s-%04x" % (kind.lower(), seq), mono_ns())
        with self._lock:
            self._jobs[h.handle_id] = (seq, kind, h.issued_ts_mono_ns, target)
        self.link.write(P.encode(ftype, seq, *payload))
        return h

    def _loop(self) -> None:
        while not self._stop.wait(0.005):
            try:
                chunk = self.link.read()
            except OSError:
                continue
            for line in self._reader.feed(chunk):
                try:
                    f = P.decode(line)
                except P.ProtocolError:
                    continue
                if f.type == RSP_POSE:
                    with self._lock:
                        self._pose = {
                            "pan_deg": f.int_field(0) / 1000.0,
                            "tilt_deg": f.int_field(1) / 1000.0,
                            "zoom": max(1.0, f.int_field(2, 100) / 100.0),
                            "moving": f.field(3, "0") == "1",
                            "focus": _FOCUS.get(f.field(4, "L"), FocusState.LOCKED),
                            "at_target": f.field(5, "0") == "1",
                        }
                        self._pose_ns = mono_ns()

    # ------------------------------------------------------------ IPTZ
    def capabilities(self) -> PTZCaps:
        return self._caps

    def set_pose(self, pan_deg: float, tilt_deg: float, zoom: float,
                 speed: PTZSpeed) -> ExecHandle:
        lo, hi = self._caps.pan_range_deg
        if not (lo - 1e-9 <= pan_deg <= hi + 1e-9):
            raise ParamOutOfRange("pan %.2f° 超出云台机械限位 [%.1f, %.1f]"
                                  % (pan_deg, lo, hi))
        lo, hi = self._caps.tilt_range_deg
        if not (lo - 1e-9 <= tilt_deg <= hi + 1e-9):
            raise ParamOutOfRange("tilt %.2f° 超出云台机械限位 [%.1f, %.1f]"
                                  % (tilt_deg, lo, hi))
        if not (1.0 - 1e-9 <= zoom <= self._caps.max_zoom + 1e-9):
            raise ParamOutOfRange("zoom %.2f× 超出光学变焦上限 %.1f×"
                                  % (zoom, self._caps.max_zoom))
        return self._send("SET_POSE", CMD_SET,
                          int(round(pan_deg * 1000)), int(round(tilt_deg * 1000)),
                          int(round(zoom * 100)),
                          "S" if speed is PTZSpeed.SLOW else "N",
                          target=(float(pan_deg), float(tilt_deg), float(zoom)))

    def set_rate(self, pan_dps: float, tilt_dps: float, ttl_ms: int) -> ExecHandle:
        if self._caps.max_pan_dps <= 0.0:
            raise NotImplementedError("本云台不支持速率控制")
        p = max(-self._caps.max_pan_dps, min(self._caps.max_pan_dps, float(pan_dps)))
        t = max(-self._caps.max_tilt_dps, min(self._caps.max_tilt_dps, float(tilt_dps)))
        # ttl 归零由云台固件执行：上位机崩了云台要自己停，不能一直转到限位
        return self._send("SET_RATE", CMD_RATE, int(round(p * 1000)),
                          int(round(t * 1000)), int(ttl_ms))

    def home(self) -> ExecHandle:
        return self._send("HOME", CMD_HOME, target=(0.0, 0.0, 1.0))

    def status(self) -> PTZStatus:
        with self._lock:
            pose, age_ns = self._pose, mono_ns() - self._pose_ns
        if pose is None:
            raise DriverNotReady("尚未收到云台位姿帧")
        stale = age_ns / 1e9 > self.status_timeout_s
        z = float(pose["zoom"])
        return PTZStatus(
            pan_deg=pose["pan_deg"], tilt_deg=pose["tilt_deg"], zoom=z,
            hfov_deg=hfov_at_zoom(self._caps.hfov_at_1x_deg, z),
            # 位姿过期时一律报"在动、未到位"：状态机等的是 at_target，
            # 拿过期数据说"到位了"会让 CAPTURE 拍到糊图
            moving=True if stale else pose["moving"],
            focus_state=pose["focus"],
            at_target=False if stale else pose["at_target"],
            ts_mono_ns=mono_ns())

    def poll(self, handle: ExecHandle) -> ExecResult:
        with self._lock:
            job = self._jobs.get(handle.handle_id)
            pose = self._pose
        if job is None:
            return ExecResult(ExecProgress.FAILED, 0, "未知句柄")
        seq, kind, t0, target = job
        ms = int((mono_ns() - t0) // 1_000_000)
        if pose is None:
            return ExecResult(ExecProgress.IN_PROGRESS, ms, None)
        if kind == "SET_RATE":
            return ExecResult(ExecProgress.DONE, ms, None)
        if pose["focus"] is FocusState.FAILED:
            return ExecResult(ExecProgress.FAILED, ms, "FOCUS_FAILED")
        # **不能只看 at_target。**刚发出 set_pose 的那一瞬间，云台还没开始动，
        # 位姿帧报的仍是上一个目标的"已到位"——照单全收会让这条指令一发出去
        # 就被判成完成，exec 进度全是假的，后面等它的人白等。
        # 判据改成"报回来的位姿确实落在本条指令的目标上"，与 at_target 无关，
        # 因此不受报文时序影响。
        if target is not None and not self._pose_matches(pose, target):
            return ExecResult(ExecProgress.IN_PROGRESS, ms, None)
        if pose["at_target"] and not pose["moving"] \
                and pose["focus"] is FocusState.LOCKED:
            return ExecResult(ExecProgress.DONE, ms, None)
        return ExecResult(ExecProgress.IN_PROGRESS, ms, None)

    #: 位姿到位判据。云台有到位抖动（桩里是 0.15°），判太严会永远不"到位"。
    POSE_TOL_DEG = 0.5
    ZOOM_TOL = 0.05

    @classmethod
    def _pose_matches(cls, pose: dict, target: tuple) -> bool:
        pan, tilt, zoom = target
        return (abs(pose["pan_deg"] - pan) <= cls.POSE_TOL_DEG
                and abs(pose["tilt_deg"] - tilt) <= cls.POSE_TOL_DEG
                and abs(pose["zoom"] - zoom) <= cls.ZOOM_TOL)

    def close(self) -> None:
        self._stop.set()
        self._thr.join(timeout=1.0)
        self.link.close()


def encode_pose_report(seq: int, *, pan_deg: float, tilt_deg: float, zoom: float,
                       moving: bool, focus: FocusState, at_target: bool) -> bytes:
    """组一帧 PST。仿真端（假云台）与真机固件都按这个格式发。"""
    ch = {FocusState.FOCUSING: "F", FocusState.LOCKED: "L", FocusState.FAILED: "X"}
    return P.encode(RSP_POSE, seq, int(round(pan_deg * 1000)),
                    int(round(tilt_deg * 1000)), int(round(zoom * 100)),
                    bool(moving), ch[focus], bool(at_target))
