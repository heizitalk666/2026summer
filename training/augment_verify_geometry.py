#!/usr/bin/env python3
"""训练增广:把"复核几何"的正常裁片补进训练集。

    python -m training.augment_verify_geometry

问题:训练集裁片来自 gen_synthetic 的密度分层采样,目标在画面里位置随机;
而系统复核时的裁片是"云台正对目标 + 变焦 + 高光最大化角度"——这个几何
处在训练分布的边缘,同一声学……同一块正常表盘,噪声/高光的随机实现决定
异常分落在 0.2 还是 1.0(实测)。系统实测的 6 张复核 ROI 因此全部越过
0.55。

做法:对每个正常目标,按复核几何(正对、多档变焦、多档俯仰、多种噪声
实现)渲染一批裁片,混进训练集。**评测集不含这些帧**(评测正常集是另种子
的 gen_synthetic,验证几何另留种子),不会把考题喂进训练。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np

from patrol.common.config import Config
from patrol.scene.render import RenderOptions, SceneRenderer
from patrol.scene.world import World

OUT = REPO / "training/datasets/normal_patches/normal"


def main() -> int:
    cfg = Config.load()
    world = World(cfg)
    OUT.mkdir(parents=True, exist_ok=True)
    normal_targets = [t for t in world.targets
                      if not t.anomalous and t.waypoint]
    import cv2
    total = 0
    jrng = np.random.default_rng(7)
    for t in normal_targets:
        wp = world.waypoints.get(t.waypoint)
        if wp is None:
            continue
        # 相机在观测位,正对该目标(pan = 目标方位,map 系 +y 为北 → 90° 基准)
        pan = 90.0
        for zoom in (1.5, 2.0, 2.5, 3.0):
            for tilt in (2.0, 3.0, 4.0):
                for seed in range(3):
                    r = SceneRenderer(world, RenderOptions(
                        width=1920, height=1080,
                        hfov_at_1x_deg=float(cfg.get("optics.hfov_at_1x_deg")),
                        simulate_4k_crop=True,
                        source_width=int(cfg.get("stub.ptz.source_width", 3840))),
                        seed=seed)
                    img, meta = r.render(pose_xy_yaw=(wp.x_m, wp.y_m, 0.0),
                                         pan_deg=pan, tilt_deg=tilt,
                                         zoom=zoom, speed_mps=0.0)
                    for m in meta:
                        if m["target_id"] != t.id:
                            continue
                        x1, y1, x2, y2 = [float(v) for v in m["bbox"]]
                        w, h = x2 - x1, y2 - y1
                        # 真值框 + 两个抖动框(模拟检测框噪声:切边/放大,
                        # 运行时 L3 收到的就是检测框,不是真值框——实测
                        # 切边 12 % 的裁片异常分从 0.5 跳到 1.0)
                        boxes = [(0.0, 0.0, 1.0, 1.0)]
                        for _ in range(2):
                            sx = float(jrng.uniform(-0.12, 0.12))
                            sy = float(jrng.uniform(-0.12, 0.12))
                            sc = float(jrng.uniform(0.95, 1.15))
                            boxes.append((sx, sy, sc, sc))
                        for bi, (sx, sy, sw, sh) in enumerate(boxes):
                            nx1 = x1 + sx * w
                            ny1 = y1 + sy * h
                            nx2 = nx1 + w * sw
                            ny2 = ny1 + h * sh
                            crop = img[max(0, int(ny1)):int(ny2),
                                       max(0, int(nx1)):int(nx2)]
                            if crop.size and min(crop.shape[:2]) >= 24:
                                ok, buf = cv2.imencode(
                                    ".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
                                if ok:
                                    (OUT / ("vgeom_%s_z%.1f_t%.1f_s%d_b%d.jpg"
                                            % (t.id, zoom, tilt, seed, bi))
                                     ).write_bytes(buf.tobytes())
                                    total += 1
    print("已补 %d 张复核几何正常裁片(含抖动框)→ %s" % (total, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
