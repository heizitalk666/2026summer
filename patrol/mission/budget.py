"""复核预算与顺延队列。ICD §7.4。

    N_max = ⌊ (T_max − L/v) / T_r ⌋

标定算例：L = 200 m，v = 0.5 m/s，T_max = 600 s，T_r = 8.8 s → N_max = 22。

**T_r 由状态机各状态的预算实时加总，不写死 8.8 s。**差异清单 A3 的条件式
三视角与 C4 的按需变焦都会改变 T_r，写死就对不上了——tools/validate.py 会
同时报告 ICD 的 8.8 s 与当前配置的实际值。

预算耗尽后 suspect.is_suspect 仍然照常置位，但 suppressed_by =
BUDGET_EXHAUSTED，事件进入顺延队列按 priority 排序，下一轮巡检优先处理。

    priority = severity × confidence × novelty

novelty 取 0.3 而不是 0，是为了让复现的缺陷仍有机会被复核，只是排在新发现
之后。取 0 会导致某个缺陷第一轮复核失败之后永远排不上队。

网关不参与预算判断，这是有意的分层：网关只管单条指令的合法性，不管任务层
的调度（ICD §7.4）。
"""
from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass, field


@dataclass(order=True)
class _QueueItem:
    sort_key: tuple
    event: dict = field(compare=False)


class VerifyBudget:
    def __init__(self, *, route_length_m: float, cruise_speed_mps: float,
                 max_run_seconds: float, state_budget_s: dict[str, float]):
        self.L = float(route_length_m)
        self.v = max(1e-6, float(cruise_speed_mps))
        self.T_max = float(max_run_seconds)
        self.state_budget = dict(state_budget_s)
        self._used = 0
        self._q: list[_QueueItem] = []
        self._tie = itertools.count()

    # ---- 预算 -------------------------------------------------------
    @property
    def T_r(self) -> float:
        """单次复核耗时 = 各状态预算之和。随 A3/C4 的开关变化。"""
        return float(sum(self.state_budget.values()))

    @property
    def n_max(self) -> int:
        cruise_s = self.L / self.v
        return max(0, math.floor((self.T_max - cruise_s) / max(1e-6, self.T_r)))

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.n_max - self._used)

    def exhausted(self) -> bool:
        return self.remaining <= 0

    def consume(self) -> None:
        self._used += 1

    def reset(self) -> None:
        self._used = 0
        self._q.clear()

    # ---- 顺延队列 ---------------------------------------------------
    def defer(self, event: dict) -> None:
        """预算耗尽时把事件压入顺延队列，按 priority 从高到低。"""
        prio = float(event.get("suspect", {}).get("priority", 0.0))
        heapq.heappush(self._q, _QueueItem((-prio, next(self._tie)), event))

    def pop_deferred(self) -> dict | None:
        return heapq.heappop(self._q).event if self._q else None

    def deferred_count(self) -> int:
        return len(self._q)

    def snapshot(self) -> dict:
        return {"T_r_s": round(self.T_r, 3), "n_max": self.n_max,
                "used": self._used, "remaining": self.remaining,
                "deferred": self.deferred_count()}


def build_budget(cfg) -> VerifyBudget:
    b = cfg.get("mission.budget")
    return VerifyBudget(route_length_m=float(b.get("route_length_m", 200.0)),
                        cruise_speed_mps=float(b.get("cruise_speed_mps", 0.5)),
                        max_run_seconds=float(b.get("max_run_seconds", 600.0)),
                        state_budget_s=dict(cfg.get("mission.fsm.budget_s")))


def priority(severity: float, confidence: float, novelty: float) -> float:
    return float(max(0.0, min(1.0, severity * confidence * novelty)))
