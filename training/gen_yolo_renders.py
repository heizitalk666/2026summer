#!/usr/bin/env python3
"""把虚拟配电室的渲染帧导出成 YOLO 格式数据集（合成集增广）。

**为什么需要这个**：公开数据集训出的检测器在巡航渲染画面上零召回——渲染域
（低照度、扁平纹理、5 m 外 50 px 小目标）不在真实照片的分布里。把渲染帧
（bbox 取渲染器真值）掺进微调集，是分工书"合成集做增广，对比开与不开"
的实现。**权重仍从公开集 best.pt 出发微调，不从合成集从头训**——纪律见
training/README.md。

划分纪律：
  train/val 按**种子与位姿栅格偏移**分——相邻渲染帧几乎全同，随机抽帧当
  val 等于开卷考。val 用另一段种子 + 0.2 m 的栅格偏移。
  tools 侧的巡航检出率探针（seed=7、步距 0.5）不进本数据集，留作独立验收。

用法：
  python -m training.gen_yolo_renders --preview 6
  python -m training.gen_yolo_renders            # 全量
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import cv2
import numpy as np

from patrol.common.config import Config
from patrol.scene.world import World
from patrol.scene.render import RenderOptions, SceneRenderer

CLASSES = ["PRESSURE_GAUGE", "INDICATOR_LIGHT", "SWITCH_HANDLE"]

# 巡航观测带：过道 y=-3.18，柜列 y=+1.82（对齐 scene.yaml 的 5 m 标定算例）
AISLE_Y = -3.18
X_LO, X_HI = 2.5, 15.5
ZOOMS = [1.0, 1.5, 2.0, 2.4, 3.0]
TRAIN_SEEDS = list(range(100, 120))
VAL_SEEDS = list(range(200, 205))


def _render_split(world, cfg, *, split: str, out: Path, preview: int) -> int:
    w = int(cfg.get("camera.width", 1920))
    h = int(cfg.get("camera.height", 1080))
    rnd = SceneRenderer(world, RenderOptions(
        width=w, height=h,
        hfov_at_1x_deg=float(cfg.get("optics.hfov_at_1x_deg", 60.0)),
        simulate_4k_crop=bool(cfg.get("stub.ptz.simulate_4k_crop", True)),
        source_width=int(cfg.get("stub.ptz.source_width", 3840)),
    ), seed=(7 if split == "val" else 3))

    img_dir = out / "images" / split
    lbl_dir = out / "labels" / split
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(42 if split == "train" else 43)
    seeds = TRAIN_SEEDS if split == "train" else VAL_SEEDS
    x0 = X_LO if split == "train" else X_LO + 0.2   # 栅格偏移防泄漏
    n = kept = 0
    previews = 0
    for zi, zoom in enumerate(ZOOMS):
        x = x0
        k = 0
        while x <= X_HI:
            seed = seeds[(k + zi * 7) % len(seeds)]
            r = SceneRenderer(world, RenderOptions(
                width=w, height=h,
                hfov_at_1x_deg=float(cfg.get("optics.hfov_at_1x_deg", 60.0)),
                simulate_4k_crop=bool(cfg.get("stub.ptz.simulate_4k_crop", True)),
                source_width=int(cfg.get("stub.ptz.source_width", 3840)),
            ), seed=seed)
            # 巡航姿态 ± 抖动（训练侧加，验证侧干净）
            jit = (lambda s: rng.uniform(-s, s)) if split == "train" else (lambda s: 0.0)
            img, meta = r.render(
                pose_xy_yaw=(x, AISLE_Y, jit(4.0)),
                pan_deg=90.0 + jit(3.0), tilt_deg=2.0 + jit(1.0),
                zoom=zoom, speed_mps=rng.choice([0.0, 0.25]) if split == "train" else 0.0)
            lines = []
            for m in meta:
                if m.get("anomalous"):
                    continue                     # FOREIGN_OBJECT 不进 L1 标签
                cls = m["defect_class"]
                if cls not in CLASSES:
                    continue
                x1, y1, x2, y2 = [float(v) for v in m["bbox"]]
                x1, y1 = max(0.0, x1), max(0.0, y1)
                x2, y2 = min(float(w) - 1, x2), min(float(h) - 1, y2)
                if x2 - x1 < 2 or y2 - y1 < 2:
                    continue
                cx, cy = (x1 + x2) / 2 / w, (y1 + y2) / 2 / h
                bw, bh = (x2 - x1) / w, (y2 - y1) / h
                lines.append("%d %.6f %.6f %.6f %.6f"
                             % (CLASSES.index(cls), cx, cy, bw, bh))
            name = "%s_z%s_x%05.1f_s%d" % (split, str(zoom).replace(".", "p"),
                                           x, seed)
            cv2.imwrite(str(img_dir / (name + ".jpg")), img)
            (lbl_dir / (name + ".txt")).write_text(
                "\n".join(lines), encoding="utf-8")
            if preview and previews < preview and split == "train" and lines:
                vis = img.copy()
                for ln in lines:
                    c, cx, cy, bw, bh = ln.split()
                    cx, cy, bw, bh = float(cx) * w, float(cy) * h, \
                        float(bw) * w, float(bh) * h
                    cv2.rectangle(vis, (int(cx - bw / 2), int(cy - bh / 2)),
                                  (int(cx + bw / 2), int(cy + bh / 2)),
                                  (0, 200, 0), 2)
                    cv2.putText(vis, CLASSES[int(c)],
                                (int(cx - bw / 2), int(cy - bh / 2) - 6),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 0), 1)
                out.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(out / ("preview_%s_%d.jpg" % (split, previews))),
                            vis)
                previews += 1
            n += 1
            kept += len(lines)
            x += 0.4
            k += 1
    return n, kept


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染帧导出 YOLO 数据集（合成增广）")
    ap.add_argument("--out", default="training/datasets/yolo_synth")
    ap.add_argument("--preview", type=int, default=0,
                    help="另存几张把标注画回图上的核对图，务必人眼看一遍")
    a = ap.parse_args()
    out = Path(a.out)
    cfg = Config.load()
    world = World(cfg)

    n_tr, box_tr = _render_split(world, cfg, split="train", out=out,
                                 preview=a.preview)
    n_va, box_va = _render_split(world, cfg, split="val", out=out, preview=0)
    (out / "data.yaml").write_text(
        "path: %s\ntrain: images/train\nval: images/val\nnc: %d\nnames: %s\n"
        % (out.resolve(), len(CLASSES), json.dumps(CLASSES)), encoding="utf-8")
    print("train %d 帧 / %d 框，val %d 帧 / %d 框 → %s"
          % (n_tr, box_tr, n_va, box_va, out.resolve()))
    print("注意：微调请从公开集权重出发（training/runs/cruise/weights/best.pt），"
          "不要从随机初始化训——纪律见 training/README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
