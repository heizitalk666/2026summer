"""复核抑制规则。ICD §7.3。

在 SUSPECT 状态检查，任一条命中则不进入复核，回 CRUISE，并在
DetectionEvent.suspect.suppressed_by 里注明。

| 规则         | 参数     | 键        | 挡的是哪类死循环                      |
|--------------|----------|-----------|---------------------------------------|
| 同目标冷却   | 60 s     | track_id  | 同一个目标反复触发                    |
| 同巡检位单次 | 2 m 半径 | pose      | 跟踪丢了 ID 之后同一目标以新 id 再触发 |
| 恢复静默     | 3 s      | 全局      | 车刚起步、云台刚归位那一瞬间的连锁触发 |

**三条规则针对的是三种不同的死循环，少任何一条都有一类补不上。**另有两条
抑制来源不属于"规则"而属于别处的判断：预算耗尽（budget.py）与定位失锁
（pose.valid = false），它们同样写进 suppressed_by。

冷却与巡检位记录随 run_id 清空，不跨轮保留。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from patrol.common.clock import mono_ns

NS = 1_000_000_000


@dataclass
class SuppressionState:
    track_cooldown_s: float = 60.0
    waypoint_radius_m: float = 2.0
    resume_silence_s: float = 3.0
    _track_last_ns: dict[int, int] = field(default_factory=dict)
    _verified_poses: list[tuple[float, float]] = field(default_factory=list)
    _resume_ns: int = 0

    # ---- 事件 -------------------------------------------------------
    def on_verify_done(self, track_id: int | None, pose_xy: tuple[float, float] | None
                       ) -> None:
        """一次复核结束（无论成功与否）后登记，用于后续抑制。"""
        now = mono_ns()
        if track_id is not None and track_id >= 0:
            self._track_last_ns[int(track_id)] = now
        if pose_xy is not None:
            self._verified_poses.append((float(pose_xy[0]), float(pose_xy[1])))

    def on_resume(self) -> None:
        """RESUME 之后进入静默期。"""
        self._resume_ns = mono_ns()

    def reset(self) -> None:
        """随 run_id 清空。不跨轮保留。"""
        self._track_last_ns.clear()
        self._verified_poses.clear()
        self._resume_ns = 0

    # ---- 判定 -------------------------------------------------------
    def check(self, *, track_id: int | None, pose_xy: tuple[float, float] | None,
              pose_valid: bool = True) -> str | None:
        """返回抑制原因，None 表示可以进入复核。

        顺序有讲究：先查定位有效性，因为定位失锁时 pose 本身不可信，
        再拿它去判"同巡检位单次"没有意义。
        """
        if not pose_valid:
            # 定位退化到纯里程计，位置会漂，GOTO_OBSERVE 在漂移的坐标系里
            # 没有意义（ICD §5.4）
            return "POSE_INVALID"

        now = mono_ns()
        if self._resume_ns and (now - self._resume_ns) < self.resume_silence_s * NS:
            return "RESUME_SILENCE"

        if track_id is not None and int(track_id) in self._track_last_ns:
            age = (now - self._track_last_ns[int(track_id)]) / NS
            if age < self.track_cooldown_s:
                return "TRACK_COOLDOWN"

        if pose_xy is not None:
            for x, y in self._verified_poses:
                if math.dist(pose_xy, (x, y)) <= self.waypoint_radius_m:
                    return "WAYPOINT_ONCE"
        return None


def build_suppression(cfg) -> SuppressionState:
    s = cfg.get("mission.suppress")
    return SuppressionState(
        track_cooldown_s=float(s.get("track_cooldown_s", 60.0)),
        waypoint_radius_m=float(s.get("waypoint_radius_m", 2.0)),
        resume_silence_s=float(s.get("resume_silence_s", 3.0)))
