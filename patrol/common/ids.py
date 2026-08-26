"""标识符生成。ICD §2.2。

| 字段     | 格式                          | 生成方       |
|----------|-------------------------------|--------------|
| run_id   | YYYYMMDD-HHMMSS-<4位随机十六进制> | mission 启动 |
| event_id | UUIDv4                        | perception   |
| cmd_id   | UUIDv4                        | mission      |
| seq      | uint32，按通道各自递增，溢出回绕 | 各发送方     |

event_id 是串起四份 Schema 的主键：同一次复核里 DetectionEvent、
由它触发的所有 ControlCommand、期间的 StatusReport、最终的
EvidencePackage 全部携带同一个 event_id。
"""
from __future__ import annotations

import itertools
import re
import secrets
import time
import uuid

UINT32_MAX = 0xFFFF_FFFF

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
WAYPOINT_RE = re.compile(r"^WP-\d{2}$")


def new_run_id(when: float | None = None) -> str:
    """一轮巡检一个。格式必须匹配 Schema 的 run_id pattern。"""
    t = time.localtime(when if when is not None else time.time())
    return "%s-%s" % (time.strftime("%Y%m%d-%H%M%S", t), secrets.token_hex(2))


def new_uuid() -> str:
    """event_id / cmd_id 用。小写无花括号的 UUIDv4。"""
    return str(uuid.uuid4())


def valid_run_id(s: str) -> bool:
    return bool(RUN_ID_RE.match(s or ""))


def valid_uuid(s: str) -> bool:
    return bool(UUID_RE.match(s or ""))


def valid_waypoint(s: str) -> bool:
    return bool(WAYPOINT_RE.match(s or ""))


class SeqCounter:
    """通道内递增序号，溢出回绕。接收方靠它检测丢包。"""

    __slots__ = ("_it",)

    def __init__(self, start: int = 0):
        self._it = itertools.count(start)

    def next(self) -> int:
        return next(self._it) & UINT32_MAX


def seq_gap(prev: int, cur: int) -> int:
    """考虑 uint32 回绕的序号差。返回丢失的报文条数（0 表示连续）。"""
    return ((cur - prev) & UINT32_MAX) - 1
