"""底盘串口协议编解码。docs/底盘串口协议.md v0.1（SER-CHASSIS-01）。

    $<TYPE>,<seq>,<payload...>*<CRC8>\\n

**这一层刻意不认识"转向角、轮速、扭矩、制动力"这些词。**协议里根本没有能
表达它们的字段，所以"AI 侧无法直接控制车辆运动"这条是由**协议本身**保证的，
不是靠代码评审保证的（ICD §4.1）。若采购到的底盘只有速度接口，适配层必须
跑在底盘 MCU 上——一旦速度指令出现在 RK3576 与底盘之间的链路上，这条保证
就没了。

编解码单独成模块，是为了让上位机驱动（chassis_serial）与假小车
（tools/fakecar）**用同一份实现**：协议的两侧共用编解码，任何一侧改了字段
另一侧立刻编译不过，不会出现"文档写的是一套、两边各实现一套"的经典事故。
校验失败的帧一律丢弃，不回执、不猜——上位机靠超时发现。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

#: CRC-8，多项式 0x07，初值 0x00。查表比逐位算快，但 115200 波特下没必要，
#: 逐位实现更容易和硬件组对答案。
_POLY = 0x07


def crc8(data: bytes) -> int:
    crc = 0x00
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ _POLY) & 0xFF if (crc & 0x80) else ((crc << 1) & 0xFF)
    return crc


class ProtocolError(ValueError):
    """帧不合法。调用方应当丢弃该帧并继续，不要中断链路。"""


@dataclass(frozen=True)
class Frame:
    type: str                    # 3 字符指令码
    seq: int                     # 0–65535，回绕
    fields: tuple[str, ...]      # payload，已按逗号切开

    def field(self, i: int, default: str = "") -> str:
        return self.fields[i] if i < len(self.fields) else default

    def int_field(self, i: int, default: int = 0) -> int:
        try:
            return int(self.fields[i])
        except (IndexError, ValueError):
            return default


def _ascii(text: str) -> str:
    """帧是 ASCII 的，非 ASCII 字符换成 `?`。

    detail 字段常常带中文（"上位机保活超时"之类），直接 encode("ascii") 会抛
    异常——而这个异常多半发生在安全事件回调里，被上层的 try/except 吞掉，
    表现为"安全事件偶尔不上报"，极难查。宁可让文字变成问号，也不能让一帧
    安全事件因为编码问题消失。真机上按协议本来也只该发 ASCII。
    """
    return text.encode("ascii", "replace").decode("ascii")


def encode(ftype: str, seq: int, *payload) -> bytes:
    """组一帧。payload 里的 None 编成 `-`，bool 编成 0/1，非 ASCII 换成 `?`。"""
    if len(ftype) != 3 or not ftype.isalpha():
        raise ProtocolError("TYPE 必须是 3 个字母，收到 %r" % ftype)
    parts = [ftype, str(int(seq) & 0xFFFF)]
    for p in payload:
        if p is None:
            parts.append("-")
        elif isinstance(p, bool):
            parts.append("1" if p else "0")
        else:
            parts.append(_ascii(str(p)))
    body = ",".join(parts)
    if "*" in body or "\n" in body:
        raise ProtocolError("payload 不得含 * 或换行: %r" % body)
    return ("$%s*%02X\n" % (body, crc8(body.encode("ascii")))).encode("ascii")


def decode(line: bytes | str) -> Frame:
    """解一帧。校验不过抛 ProtocolError。"""
    s = line.decode("ascii", "replace") if isinstance(line, bytes) else line
    s = s.strip()
    if not s.startswith("$"):
        raise ProtocolError("缺少帧起始 $: %r" % s[:40])
    star = s.rfind("*")
    if star < 0 or len(s) - star != 3:
        raise ProtocolError("缺少 *CRC8: %r" % s[:40])
    body, want = s[1:star], s[star + 1:]
    got = crc8(body.encode("ascii"))
    try:
        if int(want, 16) != got:
            raise ProtocolError("CRC 不符：帧内 %s，实算 %02X" % (want, got))
    except ValueError as e:
        raise ProtocolError("CRC 字段不是十六进制: %r" % want) from e
    parts = body.split(",")
    if len(parts) < 2:
        raise ProtocolError("字段不足: %r" % body)
    try:
        seq = int(parts[1])
    except ValueError as e:
        raise ProtocolError("seq 不是整数: %r" % parts[1]) from e
    return Frame(type=parts[0], seq=seq, fields=tuple(parts[2:]))


class LineReader:
    """把字节流切成整行。串口读到的是任意长度的片段，不是行。

    上限 4 KiB：链路上出现连续噪声时不会把内存吃光，超限直接丢弃缓冲区并
    从下一个 `$` 重新同步。
    """

    MAX = 4096

    def __init__(self) -> None:
        self._buf = bytearray()
        self.dropped = 0            # 因超长或无效被丢弃的字节数，便于观察链路质量

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        self._buf.extend(chunk)
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                if len(self._buf) > self.MAX:
                    keep = self._buf.rfind(b"$")
                    self.dropped += len(self._buf) if keep < 0 else keep
                    del self._buf[:keep if keep > 0 else len(self._buf)]
                return
            line = bytes(self._buf[:nl])
            del self._buf[:nl + 1]
            if line.strip():
                yield line


# ---------------------------------------------------------------- 语义常量
#: 上位机 → 底盘。与 ICD §4.1 白名单一一对应，没有多余的动作指令。
CMD_PAUSE = "PAU"
CMD_RESUME = "RES"
CMD_CREEP = "CRP"
CMD_GOTO = "GTO"
CMD_QUERY = "QRY"
CMD_PING = "PNG"

#: 底盘 → 上位机
RSP_ACK = "ACK"
RSP_STATUS = "STA"
RSP_SAFETY = "SAF"

#: ACK 的 result 取值
ACK_OK = "OK"
ACK_REJECT = "REJ"
ACK_BUSY = "BUSY"

#: STA 的 state 取值。与 ChassisState 同名，一一对应。
STATES = ("MOVING", "STOPPING", "STOPPED", "PAUSED", "RETURNING", "FAULT", "ESTOP")

#: SAF 的 event 取值
SAFETY_EVENTS = ("OBSTACLE_DETECTED", "BUMPER_HIT", "ESTOP_PRESSED",
                 "TILT_LIMIT", "MOTOR_FAULT", "LOW_BATTERY")


def encode_status(seq: int, *, state: str, speed_mps: float,
                  path_progress: float, distance_to_goal_m: float | None,
                  waypoint_id: str | None, battery_pct: float,
                  safety_active: bool) -> bytes:
    """组一帧 STA。协议里全部用整数（毫米、千分比），避免浮点格式扯皮。"""
    return encode(
        RSP_STATUS, seq, state,
        int(round(speed_mps * 1000.0)),
        int(round(path_progress * 1000.0)),
        -1 if distance_to_goal_m is None else int(round(distance_to_goal_m * 1000.0)),
        waypoint_id or "-",
        int(round(battery_pct * 10.0)),
        bool(safety_active))


def parse_status(f: Frame) -> dict:
    """STA → 与 ChassisStatus 同构的字典。"""
    state = f.field(0, "FAULT")
    if state not in STATES:
        raise ProtocolError("未知底盘状态 %r" % state)
    wp = f.field(4, "-")
    d = f.int_field(3, -1)
    return {
        "state": state,
        "speed_mps": f.int_field(1) / 1000.0,
        "path_progress": f.int_field(2) / 1000.0,
        "distance_to_goal_m": None if d < 0 else d / 1000.0,
        "current_waypoint_id": None if wp in ("", "-") else wp,
        "battery_pct": f.int_field(5) / 10.0,
        "safety_layer_active": f.field(6, "0") == "1",
    }


def encode_safety(seq: int, *, event: str, severity: str, brake_ms: int,
                  detail: str) -> bytes:
    return encode(RSP_SAFETY, seq, event, severity, int(brake_ms),
                  detail.replace(",", ";")[:120])


def parse_safety(f: Frame) -> dict:
    """SAF → 与 chassis_stub 的安全事件同构的字典。

    **brake_ms 超过 100 ms 也照样上报，不因超限丢帧。**那个值恰好是最该
    留证的东西（docs/底盘串口协议.md §3.4）。
    """
    return {
        "event_type": f.field(0, "MOTOR_FAULT"),
        "severity": f.field(1, "CRITICAL"),
        "source": "CHASSIS_SAFETY_LAYER",
        "action_taken": "BRAKE",
        "brake_latency_ms": f.int_field(2, 0),
        "detail": f.field(3, ""),
    }
