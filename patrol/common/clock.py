"""双时间戳。

ICD §2.1：每条报文带两个时间戳，用途不同，不可互相替代。

- ts_mono_ns  单调时钟纳秒，用于算时延、超时判定、时序对齐
- ts_utc_ms   UTC 毫秒，用于证据包时间标记、云端展示、日志检索

超时判定一律用 ts_mono_ns。系统时间在 NTP 同步时会跳变，用它算超时会在
同步瞬间产生几百毫秒的假超时，正好落在安全响应的量级上。
"""
from __future__ import annotations

import time

NS_PER_MS = 1_000_000


def mono_ns() -> int:
    """单调时钟，纳秒。对应 CLOCK_MONOTONIC。"""
    return time.monotonic_ns()


def utc_ms() -> int:
    """UTC 墙上时间，毫秒。"""
    return int(time.time() * 1000)


def stamps() -> tuple[int, int]:
    """一次取到两个时间戳，保证同一时刻。"""
    return mono_ns(), utc_ms()


def elapsed_ms(since_mono_ns: int) -> int:
    """自某个单调时刻起经过的毫秒数。"""
    return (mono_ns() - since_mono_ns) // NS_PER_MS


class Deadline:
    """单调时钟上的超时判定。

    状态机每个状态的超时都用它，不用 time.time()。
    """

    __slots__ = ("_end_ns", "_start_ns")

    def __init__(self, timeout_ms: float):
        self._start_ns = mono_ns()
        self._end_ns = self._start_ns + int(timeout_ms * NS_PER_MS)

    def expired(self) -> bool:
        return mono_ns() >= self._end_ns

    def remaining_ms(self) -> int:
        return max(0, (self._end_ns - mono_ns()) // NS_PER_MS)

    def elapsed_ms(self) -> int:
        return (mono_ns() - self._start_ns) // NS_PER_MS
