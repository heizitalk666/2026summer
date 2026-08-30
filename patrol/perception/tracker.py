"""目标跟踪。方案书 §5.2 / §6.4。

用 IoU 匹配 + 卡尔曼式的简单预测。ByteTrack 的完整实现对本课题是过度设计：
巡航速度 0.5 m/s、10 fps 时帧间车辆位移 5 cm，对 3–8 m 处的目标，帧间交并比
保持在 0.8 以上，简单 IoU 匹配就够。

**跟踪不断，同一目标就不会被反复当作新目标触发重复测量**——这是三条抑制
规则里"同目标冷却"的前提。跟踪断链时目标会拿到新的 track_id，那时靠第二条
"同巡检位 2 m 半径内本轮只测一次"兜住。两条规则针对的是不同的失效路径。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from patrol.perception.detector.base import Detection


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    ub = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    denom = ua + ub - inter
    return float(inter / denom) if denom > 1e-9 else 0.0


@dataclass
class Track:
    track_id: int
    defect_class: str
    bbox: tuple
    age: int = 0             # 连续未匹配的帧数
    hits: int = 1
    velocity: tuple = (0.0, 0.0)
    history: list = field(default_factory=list)

    def predict(self) -> tuple:
        vx, vy = self.velocity
        x1, y1, x2, y2 = self.bbox
        return (x1 + vx, y1 + vy, x2 + vx, y2 + vy)


class IouTracker:
    def __init__(self, iou_threshold: float = 0.3, max_age: int = 15):
        self.thr = float(iou_threshold)
        self.max_age = int(max_age)
        self._tracks: dict[int, Track] = {}
        self._next_id = 1

    def reset(self) -> None:
        """跨轮巡检清空。冷却与巡检位记录随 run_id 清空，不跨轮保留。"""
        self._tracks.clear()
        self._next_id = 1

    def update(self, dets: list[Detection]) -> list[Detection]:
        """就地给每个检出分配 track_id，返回同一列表。"""
        unmatched = list(range(len(dets)))
        for t in self._tracks.values():
            t.age += 1

        # 贪心匹配：按 IoU 从高到低。目标数是个位数，不值得上匈牙利算法。
        pairs = []
        for tid, t in self._tracks.items():
            pred = t.predict()
            for i in unmatched:
                if dets[i].defect_class != t.defect_class:
                    continue
                v = iou(pred, dets[i].bbox)
                if v >= self.thr:
                    pairs.append((v, tid, i))
        pairs.sort(reverse=True)
        used_t, used_d = set(), set()
        for v, tid, i in pairs:
            if tid in used_t or i in used_d:
                continue
            used_t.add(tid)
            used_d.add(i)
            t = self._tracks[tid]
            ox = (t.bbox[0] + t.bbox[2]) / 2.0
            oy = (t.bbox[1] + t.bbox[3]) / 2.0
            nx = dets[i].cx
            ny = dets[i].cy
            t.velocity = (0.6 * t.velocity[0] + 0.4 * (nx - ox),
                          0.6 * t.velocity[1] + 0.4 * (ny - oy))
            t.bbox = dets[i].bbox
            t.age = 0
            t.hits += 1
            dets[i].track_id = tid

        for i in range(len(dets)):
            if i in used_d:
                continue
            tid = self._next_id
            self._next_id += 1
            self._tracks[tid] = Track(track_id=tid, defect_class=dets[i].defect_class,
                                      bbox=dets[i].bbox)
            dets[i].track_id = tid

        for tid in [k for k, t in self._tracks.items() if t.age > self.max_age]:
            self._tracks.pop(tid, None)
        return dets

    @property
    def active(self) -> int:
        return len(self._tracks)
