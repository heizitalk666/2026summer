"""三条抑制规则 + 两条来自别处的抑制。ICD §7.3。

**三条规则针对的是三种不同的死循环，少任何一条都有一类补不上。**这个文件
就按"每条规则各构造一次它专属的死循环"来组织，每个用例先造出死循环，再验
规则确实把它挡住了。
"""
from __future__ import annotations

import time

import pytest

from patrol.common.config import Config
from patrol.mission.suppress import SuppressionState, build_suppression


@pytest.fixture()
def sup():
    return SuppressionState(track_cooldown_s=60.0, waypoint_radius_m=2.0,
                            resume_silence_s=3.0)


def test_clean_state_lets_everything_through(sup):
    assert sup.check(track_id=7, pose_xy=(10.0, -3.18)) is None


# ---------------------------------------------------------------- 三条规则
def test_track_cooldown_blocks_the_same_target_re_triggering(sup):
    """死循环一：同一个目标复核完立刻又触发，车原地反复停车。"""
    sup.on_verify_done(track_id=7, pose_xy=None)
    assert sup.check(track_id=7, pose_xy=None) == "TRACK_COOLDOWN"
    assert sup.check(track_id=8, pose_xy=None) is None, "别的目标不该被牵连"


def test_waypoint_once_catches_the_target_that_got_a_new_track_id(sup):
    """死循环二：跟踪断链后同一目标以新 id 再触发，冷却规则认不出来。

    这正是"同巡检位 2 m 半径内本轮只测一次"要补的那类失效——两条规则针对
    的是不同的失效路径，不是一条的加强版。
    """
    sup.on_verify_done(track_id=7, pose_xy=(10.0, -3.18))
    assert sup.check(track_id=999, pose_xy=(10.4, -3.18)) == "WAYPOINT_ONCE"
    assert sup.check(track_id=999, pose_xy=(13.0, -3.18)) is None, "2 m 外应放行"


def test_resume_silence_blocks_the_chain_reaction_right_after_resume(sup):
    """死循环三：车刚起步、云台刚归位那一瞬间的连锁触发。

    此时画面在剧烈变化，检出置信度普遍落在 0.25–0.60 的复核带里，不挡住
    就会一路触发下去。
    """
    sup.on_resume()
    assert sup.check(track_id=1, pose_xy=(0.0, 0.0)) == "RESUME_SILENCE"


def test_resume_silence_expires(sup):
    sup.resume_silence_s = 0.05
    sup.on_resume()
    time.sleep(0.08)
    assert sup.check(track_id=1, pose_xy=(0.0, 0.0)) is None


# ---------------------------------------------------------------- 顺序
def test_pose_invalid_is_checked_first(sup):
    """定位失锁时 pose 本身不可信，再拿它判"同巡检位"没有意义。

    顺序错了会得到 WAYPOINT_ONCE 这种误导性的原因，排查时会往错的方向找。
    """
    sup.on_verify_done(track_id=7, pose_xy=(10.0, -3.18))
    sup.on_resume()
    assert sup.check(track_id=7, pose_xy=(10.0, -3.18),
                     pose_valid=False) == "POSE_INVALID"


def test_reset_clears_everything_between_runs(sup):
    """冷却与巡检位记录随 run_id 清空，不跨轮保留。

    跨轮保留的话第二轮巡检会把第一轮测过的点全部跳过，等于只巡一轮。
    """
    sup.on_verify_done(track_id=7, pose_xy=(10.0, -3.18))
    sup.on_resume()
    sup.reset()
    assert sup.check(track_id=7, pose_xy=(10.0, -3.18)) is None


def test_build_from_config_matches_yaml():
    cfg = Config.load()
    s = build_suppression(cfg)
    y = cfg.get("mission.suppress")
    assert s.track_cooldown_s == float(y["track_cooldown_s"])
    assert s.waypoint_radius_m == float(y["waypoint_radius_m"])
    assert s.resume_silence_s == float(y["resume_silence_s"])


def test_suppression_reasons_are_all_in_the_frozen_schema():
    """suppressed_by 的取值必须落在冻结 Schema 的枚举里，否则报文发不出去。"""
    import json
    from pathlib import Path
    schema = json.loads(Path("patrol/schemas/detection_event.schema.json")
                        .read_text(encoding="utf-8"))
    allowed = set(schema["properties"]["suspect"]["properties"]
                  ["suppressed_by"]["oneOf"][1]["enum"])
    produced = {"TRACK_COOLDOWN", "WAYPOINT_ONCE", "RESUME_SILENCE",
                "POSE_INVALID", "BUDGET_EXHAUSTED"}
    assert produced <= allowed, "抑制原因 %s 不在 Schema 枚举内" % (produced - allowed)
