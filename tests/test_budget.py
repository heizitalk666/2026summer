"""复核预算 N_max 与顺延队列。ICD §7.4。

    N_max = ⌊ (T_max − L/v) / T_r ⌋

标定算例：L = 200 m，v = 0.5 m/s，T_max = 600 s，T_r = 9.2 s → N_max = 21。

**T_r 不写死。**差异清单 A3 的三种采集模式与 C4 的按需变焦都会改变单次复核
耗时，写死 9.2 s 会让预算和实际对不上：模式一改，预算还按老数算，跑到一半
才发现时间不够。这里把"T_r 随配置变化"这件事钉住。
"""
from __future__ import annotations

import math

import pytest

from patrol.common.config import Config
from patrol.mission.budget import VerifyBudget, build_budget

ICD_BUDGET = {"SUSPECT": 0.3, "HALT_REQ": 2.0, "AIM": 1.5, "ZOOM": 1.5,
              "CAPTURE": 0.6, "VERIFY": 2.5, "PACK": 0.5, "RESUME": 0.3}


def make(**kw) -> VerifyBudget:
    args = dict(route_length_m=200.0, cruise_speed_mps=0.5, max_run_seconds=600.0,
                state_budget_s=dict(ICD_BUDGET))
    args.update(kw)
    return VerifyBudget(**args)


# ---------------------------------------------------------------- 算例
def test_icd_worked_example_reproduces():
    """ICD §7.4 的标定算例必须算得出来，否则文档与实现已经分家。"""
    b = make()
    assert b.T_r == pytest.approx(9.2, abs=1e-9), "各状态预算之和"
    # ICD v2.0 正文写的是 T_r = 9.2 s → N_max = 21；按本仓库的预算表（CAPTURE 按
    # 差异清单 A3 放宽）实际是 9.2 s → 21。差值本身就是 A3 要评审的内容，
    # 所以两个数都写在这里，改了哪一个都会被这条用例拦下来。
    assert b.n_max == math.floor((600.0 - 400.0) / 9.2) == 21
    icd = make(state_budget_s=dict(ICD_BUDGET, CAPTURE=0.2))
    assert icd.T_r == pytest.approx(8.8) and icd.n_max == 22


def test_config_and_code_agree():
    cfg = Config.load()
    b = build_budget(cfg)
    fsm = cfg.get("mission.fsm.budget_s")
    assert b.T_r == pytest.approx(sum(fsm.values()))
    assert b.n_max == b.snapshot()["n_max"]


def test_longer_capture_mode_shrinks_the_budget():
    """A3 换成无条件三视角，单次复核变慢，N_max 必须跟着变小。"""
    fast = make()
    slow = make(state_budget_s=dict(ICD_BUDGET, CAPTURE=2.1))
    assert slow.T_r > fast.T_r
    assert slow.n_max < fast.n_max


def test_budget_cannot_go_negative():
    """路线长到走完就超时的情形，N_max 是 0 而不是负数。"""
    b = make(route_length_m=2000.0)
    assert b.n_max == 0 and b.exhausted()


# ---------------------------------------------------------------- 消耗
def test_consume_until_exhausted():
    b = make()
    n = b.n_max
    for _ in range(n):
        assert not b.exhausted()
        b.consume()
    assert b.exhausted() and b.remaining == 0


def test_reset_restores_budget_between_runs():
    b = make()
    b.consume()
    b.defer({"suspect": {"priority": 0.5}})
    b.reset()
    assert b.used == 0 and b.deferred_count() == 0


# ---------------------------------------------------------------- 顺延队列
def _ev(prio: float, tag: str) -> dict:
    return {"tag": tag, "suspect": {"priority": prio}}


def test_deferred_queue_pops_highest_priority_first():
    b = make()
    for p, t in ((0.21, "低"), (0.87, "高"), (0.55, "中")):
        b.defer(_ev(p, t))
    assert [b.pop_deferred()["tag"] for _ in range(3)] == ["高", "中", "低"]


def test_equal_priority_keeps_insertion_order():
    """同优先级按先来后到，不能因为堆的实现细节变成随机顺序。

    随机顺序会让"某个缺陷第一轮排不上、第二轮还排不上"变成可能。
    """
    b = make()
    for i in range(5):
        b.defer(_ev(0.4, "第%d个" % i))
    assert [b.pop_deferred()["tag"] for _ in range(5)] == ["第%d个" % i for i in range(5)]


def test_pop_on_empty_queue_returns_none():
    assert make().pop_deferred() is None


def test_novelty_0_3_keeps_repeat_defects_in_the_queue():
    """复现的缺陷 novelty=0.3，排在新发现之后但**仍在队里**。

    取 0 的话，某个缺陷第一轮复核失败之后就永远排不上队了——这正是把
    novelty 定成 0.3 而不是 0 的理由，用一条用例把它固定下来。
    """
    b = make()
    new_one = 0.70 * 0.45 * 1.0          # severity × confidence × novelty
    repeat = 0.70 * 0.45 * 0.3
    b.defer(_ev(repeat, "复现"))
    b.defer(_ev(new_one, "新发现"))
    assert repeat > 0.0
    assert [b.pop_deferred()["tag"] for _ in range(2)] == ["新发现", "复现"]
