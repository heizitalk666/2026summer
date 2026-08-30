"""心跳看门狗。ICD §4.5。

| 项           | 值                                                      |
|--------------|---------------------------------------------------------|
| 心跳频率     | 5 Hz（200 ms 一条）                                     |
| 超时判定     | 1500 ms 内没收到任何 HEARTBEAT                          |
| 超时动作     | 网关自行下发 RESUME，issued_by = WATCHDOG               |
| 恢复条件     | 心跳恢复且连续 3 条正常                                 |

**看门狗的动作是让车继续走完巡检路线，不是让车停住。**理由：AI 进程崩了的
时候，车停在配电室通道中间比走完路线回充电位更麻烦。巡检路线本身是标定过
的安全路径，底盘沿着它走不需要 AI 参与。真正需要立刻停车的情况由底盘安全层
处理，那条通路不经过 AI 也不经过网关。

超时判定一律用单调钟。系统时间在 NTP 同步时会跳变，用它算超时会在同步瞬间
产生几百毫秒的假超时，正好落在安全响应的量级上。
"""
from __future__ import annotations

from patrol.common.clock import mono_ns
from patrol.gateway import limits as L


class Watchdog:
    def __init__(self, timeout_ms: int = L.HEARTBEAT_TIMEOUT_MS,
                 recover_count: int = L.HEARTBEAT_RECOVER_COUNT):
        self.timeout_ns = int(timeout_ms) * 1_000_000
        self.recover_count = int(recover_count)
        self._last_hb_ns = mono_ns()
        self._triggered = False
        self._good_streak = 0
        # **收到第一条心跳之前不计时。**开机时 mission 还没连上来，此时
        # 就开始倒计时会在启动瞬间必然误触发看门狗——车还没跑就先被判定
        # "AI 失联"。看门狗监视的是"曾经在、现在断了"，不是"从来没来过"。
        self._armed = False

    # -- 事件 ---------------------------------------------------------
    def on_heartbeat(self) -> bool:
        """收到一条心跳。返回 True 表示本次调用解除了看门狗态。"""
        self._last_hb_ns = mono_ns()
        self._armed = True
        if not self._triggered:
            self._good_streak = 0
            return False
        self._good_streak += 1
        if self._good_streak >= self.recover_count:
            self._triggered = False
            self._good_streak = 0
            return True
        return False

    def check(self) -> bool:
        """周期调用。返回 True 表示**本次刚刚**判定超时（只触发一次）。"""
        if self._triggered or not self._armed:
            return False
        if self.age_ms() * 1_000_000 >= self.timeout_ns:
            self._triggered = True
            self._good_streak = 0
            return True
        return False

    # -- 查询 ---------------------------------------------------------
    def age_ms(self) -> int:
        return int((mono_ns() - self._last_hb_ns) // 1_000_000)

    @property
    def triggered(self) -> bool:
        return self._triggered

    @property
    def armed(self) -> bool:
        """是否已经收到过至少一条心跳。"""
        return self._armed

    @property
    def heartbeat_ok(self) -> bool:
        if not self._armed:
            return True          # 还没连上来，不算异常
        return not self._triggered and self.age_ms() * 1_000_000 < self.timeout_ns

    def snapshot(self) -> dict:
        """StatusReport.watchdog 块。"""
        return {
            "heartbeat_ok": bool(self.heartbeat_ok),
            "last_heartbeat_age_ms": min(self.age_ms(), 0xFFFF_FFFF),
            "watchdog_triggered": bool(self._triggered),
        }
