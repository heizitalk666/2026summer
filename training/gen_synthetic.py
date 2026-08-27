#!/usr/bin/env python3
"""从渲染器直接生成带标注的合成数据集。

    python -m training.gen_synthetic --n 400 --out training/datasets/synth
    python -m training.gen_synthetic --n 40 --preview 8      # 顺便存核对图

**为什么值得做这件事。**方案书 §2.4.5 承认了一个绕不开的约束：配电室缺陷
没有公开的标注数据集，尤其是像素级的。而这套系统的读数通路恰恰要的就是
像素级——指针占几个像素、边界在哪，直接决定读数误差。真实标注一块表盘要
人描十几分钟，四百张就是一周。

渲染器把这件事变成了免费的：图是按几何画出来的，所以**画的时候就知道每个
像素属于谁**。`SceneRenderer.render(want_mask=True)` 用同一套 uv、同一次
透视变换出掩膜，与图像天生逐像素对齐，不需要任何配准。

一次生成四种标注，对应四类模型里的三类（OCR 用现成权重，不训）：

    labels/   YOLO 检测框            → L1 检测
    masks/    指针/表面/刻度 三类掩膜   → L2 分割
    ocr.jsonl 表盘 ROI 与其上印着的字  → L2' 互证通路的评测集
    normal/   无缺陷样本裁片           → L3 非监督异常的正常集

**采样按像素密度分层，不是按位置均匀撒。**这是本项目最关键的一个数据设计
决定：整套方案的立论是"5 m 处 1× 只有 50 px 读不准，要停车变焦到 120 px"，
所以训练集必须同时覆盖这两段。按位置均匀采会让样本挤在巡航态那一端，
复核态（大目标、浅景深、有透视）的样本严重不足——而那正是读数真正发生的
地方。做法是先抽一个目标像素密度，再反解需要多大变焦。

**诚实的边界**：合成数据训出来的模型在真实配电室上会有 sim-to-real gap。
这里做的域随机化只有位姿/角度/变焦/光照/运动模糊/高光这几维，纹理和背景
的多样性远不如真实场景。正确的用法是拿它做预训练与算法验证，真机数据到了
再微调。别拿合成集上的 mAP 去当验收指标。
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from patrol.common.config import Config
from patrol.scene.gauges import SEG_NAMES
from patrol.scene.optics import pixel_density
from patrol.scene.render import RenderOptions, SceneRenderer
from patrol.scene.world import World

#: 检测类别顺序。**这个顺序就是 YOLO 标签里的类别号，改了旧标签全废**，
#: 所以与 configs/system.yaml 的 mission.first_release_classes 对齐后固定下来。
CLASSES = ["PRESSURE_GAUGE", "INDICATOR_LIGHT", "SWITCH_HANDLE",
           "OIL_LEVEL_GAUGE", "FOREIGN_OBJECT"]

#: 目标像素密度的采样区间。下限压到 25 px 是有意的——巡航态远处的表就是这么
#: 小，模型必须见过"小到读不出来"的样子，才学得会在那种情况下别硬猜。
#: 上限 220 px 略高于判据线 120 px，覆盖复核到位之后的样子。
DENSITY_LO, DENSITY_HI = 25.0, 220.0


def zoom_for(width: int, size_m: float, dist_m: float, want_px: float,
             hfov1x: float, zmax: float) -> float:
    """反解：要让这个目标占到 want_px，需要多大变焦。

    p = W·D·z / (2·d·tan(θ₀/2))，对 z 是线性的，所以直接除。
    """
    p1 = pixel_density(width, size_m, 1.0, dist_m, hfov1x)
    if p1 <= 1e-6:
        return 1.0
    return float(np.clip(want_px / p1, 1.0, zmax))


def look_at(px: float, py: float, cx: float, cy: float, yaw_deg: float) -> float:
    """要让相机对准 (px, py)，云台 pan 该是多少（相对车头）。"""
    az = math.degrees(math.atan2(py - cy, px - cx))
    return (az - yaw_deg + 180.0) % 360.0 - 180.0


def sample_viewpoints(cfg, world: World, n: int, rng: random.Random,
                      *, width: int, hfov1x: float, zmax: float) -> list[dict]:
    """先抽目标像素密度，再挑一个够得着这个密度的（航点, 目标）对。

    **顺序不能反。**第一版写的是"随便挑一对，再反解变焦"，结果 99 % 的样本
    落在判据线以下——因为过道到柜面 5.00 m，1280 宽下一块 0.18 m 的表在 3×
    也只有 120 px，而大多数（航点, 目标）对比这更远，变焦顶到头也够不着。
    自检报告一眼就看出来了（"低于判据线 99 %"），否则这批数据会安安静静地
    训出一个只见过小目标的模型。

    改成先抽密度，再从"在 zmax 下够得着这个密度"的组合里挑，两段才真的都
    覆盖到。够不着任何一个时退而求其次取最近的一对，并如实计入统计——
    强行凑数会让分布图说谎。
    """
    wps = list(cfg.get("waypoints") or [])
    targets = list(world.targets)
    if not wps or not targets:
        raise SystemExit("配置里没有航点或目标，无法生成数据集")

    # 每个（航点, 目标）对在 zmax 下能达到的最大密度，算一次就够
    pairs = []
    for wp in wps:
        wx, wy = float(wp["x_m"]), float(wp["y_m"])
        for t in targets:
            d = math.hypot(float(t.position[0]) - wx, float(t.position[1]) - wy)
            if d < 0.8:
                continue
            pairs.append((wp, t, d,
                          pixel_density(width, t.diameter_m, zmax, d, hfov1x)))
    if not pairs:
        raise SystemExit("没有可用的（航点, 目标）组合")

    out = []
    for _ in range(n):
        want = rng.uniform(DENSITY_LO, DENSITY_HI)
        cand = [pr for pr in pairs if pr[3] >= want]
        wp, t, _d0, _pmax = rng.choice(cand) if cand else min(pairs, key=lambda pr: pr[2])
        # 位姿抖动：车不会精确停在航点上，模型不该只见过标称位姿
        x = float(wp["x_m"]) + rng.uniform(-0.45, 0.45)
        y = float(wp["y_m"]) + rng.uniform(-0.18, 0.18)
        yaw = float(wp.get("yaw_deg", 0.0)) + rng.uniform(-4.0, 4.0)
        tx, ty = float(t.position[0]), float(t.position[1])
        d = max(0.8, math.hypot(tx - x, ty - y))
        z = zoom_for(width, t.diameter_m, d, want, hfov1x, zmax)
        # 对不准也要见过：整幅都对得死死的会让模型学到"目标总在正中"
        pan = look_at(tx, ty, x, y, yaw) + rng.uniform(-6.0, 6.0)
        tilt = math.degrees(math.atan2(
            float(t.position[2]) - world.camera_height_m, max(0.3, d))
        ) + rng.uniform(-2.0, 2.0)
        out.append({"pose": (x, y, yaw), "pan": pan, "tilt": tilt, "zoom": z,
                    "speed": rng.choice([0.0, 0.0, 0.15, 0.25, 0.35]),
                    "aim_id": t.id, "want_px": want})
    return out


def yolo_line(cls_idx: int, box, W: int, H: int) -> str | None:
    x1, y1, x2, y2 = [float(v) for v in box]
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(float(W), x2), min(float(H), y2)
    bw, bh = x2 - x1, y2 - y1
    if bw < 3.0 or bh < 3.0:
        return None                        # 太小的框对训练是噪声
    return "%d %.6f %.6f %.6f %.6f" % (
        cls_idx, (x1 + x2) / 2 / W, (y1 + y2) / 2 / H, bw / W, bh / H)


def dial_texts(priors: dict) -> list[str]:
    """这块表面上**印着**的字。渲染器画的就是这些，所以是精确真值。

    与 scene/gauges.render_pointer_gauge 的画法保持一致：量程四等分五个数字
    标签，加一个单位串。
    """
    lo, hi = priors.get("range_min"), priors.get("range_max")
    if lo is None or hi is None:
        return []
    txt = ["%g" % round(float(lo) + i / 4.0 * (float(hi) - float(lo)), 2)
           for i in range(5)]
    if priors.get("unit"):
        txt.append(str(priors["unit"]))
    return txt


def draw_check(img, meta, mask):
    """把标注画回图上，供人抽样目视核对。

    **这一步不能省。**掩膜错位、类别号错位这类问题在数字上完全看不出来
    （分布、数量、比例全都正常），只有把标签画回图上才一眼看得见。
    """
    vis = img.copy()
    color = np.zeros_like(vis)
    color[mask == 1] = (90, 140, 60)
    color[mask == 2] = (60, 60, 235)
    color[mask == 3] = (200, 160, 60)
    vis = cv2.addWeighted(vis, 0.72, color, 0.28, 0)
    for m in meta:
        x1, y1, x2, y2 = [int(round(v)) for v in m["bbox"]]
        cv2.rectangle(vis, (x1, y1), (x2, y2), (255, 255, 255), 1)
        cv2.putText(vis, "%s p=%.0f" % (m["defect_class"][:12], m["p_px"]),
                    (x1, max(12, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                    (255, 255, 255), 1, cv2.LINE_AA)
    return vis


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="从渲染器生成带标注的合成数据集")
    ap.add_argument("--n", type=int, default=200, help="总帧数")
    ap.add_argument("--out", default="training/datasets/synth")
    # **默认与 configs 里的相机分辨率一致。**像素密度 p = W·D·z/(2d·tan(θ₀/2))
    # 里的 W 就是帧宽，用 1280 生成的数据集，它的 p 值和车上跑出来的
    # 差 1.5 倍，"够不够 120 px"这个判据整体失准且毫无征兆。
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--height", type=int, default=None)
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--preview", type=int, default=6,
                    help="另存几张把标注画回图上的核对图")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)

    cfg = Config.load(a.config)
    a.width = a.width or int(cfg.get("camera.width", 1920))
    a.height = a.height or int(cfg.get("camera.height", 1080))
    rng = random.Random(a.seed)
    world = World(cfg)
    hfov1x = float(cfg.get("optics.hfov_at_1x_deg", 60.0))
    zmax = float(cfg.get("optics.max_zoom", 3.0))
    renderer = SceneRenderer(
        world, RenderOptions(width=a.width, height=a.height, draw_debug=False),
        seed=a.seed)

    root = Path(a.out)
    for sub in ("images/train", "images/val", "labels/train", "labels/val",
                "masks/train", "masks/val", "normal", "check"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    views = sample_viewpoints(cfg, world, a.n, rng, width=a.width,
                              hfov1x=hfov1x, zmax=zmax)
    counts, dens, aim_dens, ocr_rows, n_masked = Counter(), [], [], [], 0
    n_val = 0
    for i, v in enumerate(views):
        split = "val" if rng.random() < a.val_frac else "train"
        n_val += (split == "val")
        img, meta, mask = renderer.render(
            pose_xy_yaw=v["pose"], pan_deg=v["pan"], tilt_deg=v["tilt"],
            zoom=v["zoom"], speed_mps=v["speed"], want_mask=True)
        stem = "%06d" % i
        lines = []
        for m in meta:
            m["p_px"] = pixel_density(a.width, m["target_size_m"], v["zoom"],
                                      m["distance_m"], hfov1x)
            cls = m["defect_class"]
            if cls not in CLASSES:
                continue
            ln = yolo_line(CLASSES.index(cls), m["bbox"], a.width, a.height)
            if ln is None:
                continue
            lines.append(ln)
            counts[cls] += 1
            dens.append(m["p_px"])
            if m["target_id"] == v.get("aim_id"):
                aim_dens.append(m["p_px"])
            t = world.by_id(m["target_id"])
            if t is not None and str(t.priors.get("kind")) == "POINTER_GAUGE":
                texts = dial_texts(t.priors)
                if texts and m["p_px"] >= 40.0:
                    ocr_rows.append({
                        "image": "images/%s/%s.jpg" % (split, stem),
                        "roi": [round(float(x), 1) for x in m["bbox"]],
                        "texts": texts, "unit": t.priors.get("unit"),
                        "range": [t.priors.get("range_min"),
                                  t.priors.get("range_max")],
                        "pixel_density_px": round(float(m["p_px"]), 1)})

        cv2.imwrite(str(root / "images" / split / (stem + ".jpg")), img,
                    [cv2.IMWRITE_JPEG_QUALITY, 90])
        (root / "labels" / split / (stem + ".txt")).write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        cv2.imwrite(str(root / "masks" / split / (stem + ".png")), mask)
        n_masked += int((mask > 0).any())

        # L3 的正常集：只收无缺陷目标的裁片。**异常样本一张都不能混进来**，
        # 非监督异常检测学的就是"正常长什么样"，混一张进去就是给它下毒。
        for m in meta:
            t = world.by_id(m["target_id"])
            if t is None or t.anomalous or m.get("p_px", 0) < 60.0:
                continue
            x1, y1, x2, y2 = [int(round(x)) for x in m["bbox"]]
            x1, y1 = max(0, x1), max(0, y1)
            crop = img[y1:min(a.height, y2), x1:min(a.width, x2)]
            if crop.size and min(crop.shape[:2]) >= 24:
                cv2.imwrite(str(root / "normal" /
                                ("%s_%s.jpg" % (stem, m["target_id"]))), crop)

        if i < a.preview:
            cv2.imwrite(str(root / "check" / (stem + ".jpg")),
                        draw_check(img, meta, mask))

    (root / "ocr.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ocr_rows),
        encoding="utf-8")
    (root / "data.yaml").write_text(
        "# 由 training/gen_synthetic.py 生成，勿手改\n"
        "path: %s\ntrain: images/train\nval: images/val\n"
        "nc: %d\nnames: [%s]\n"
        % (root.resolve(), len(CLASSES), ", ".join(CLASSES)), encoding="utf-8")
    (root / "seg_classes.txt").write_text(
        "\n".join("%d %s" % (i, n) for i, n in enumerate(SEG_NAMES)) + "\n",
        encoding="utf-8")

    # ---- 自检报告。数字不对就别拿去训 ----
    print("\n生成 %d 帧（train %d / val %d），标注写到 %s/"
          % (len(views), len(views) - n_val, n_val, root))
    print("\n类别分布")
    for c in CLASSES:
        print("  %-18s %5d" % (c, counts.get(c, 0)))
    def hist(vals, title):
        if not vals:
            return
        d = np.asarray(vals)
        print("\n%s（判据线 120 px，共 %d 个）" % (title, len(d)))
        edges = [0, 30, 50, 80, 120, 160, 1e9]
        names = ["<30", "30–50", "50–80", "80–120", "120–160", "≥160"]
        for lo, hi, nm in zip(edges, edges[1:], names):
            k = int(((d >= lo) & (d < hi)).sum())
            print("  %-9s %5d  %s" % (nm, k, "█" * int(40 * k / max(1, len(d)))))
        below = int((d < 120).sum())
        print("  低于判据线 %.0f %%，达标 %.0f %%"
              % (100 * below / len(d), 100 * (1 - below / len(d))))
        return below, len(d)

    hist(dens, "像素密度分布 · 全部标注目标")
    got = hist(aim_dens, "像素密度分布 · 采样时瞄准的那个目标")
    if got and (got[0] == 0 or got[0] == got[1]):
        print("  ⚠ 只覆盖了一侧——整套方案的立论是这两段都要见过。"
              "检查 DENSITY_LO/HI、场景距离与 optics.max_zoom 是否匹配")
    print("\n掩膜非空 %d/%d 帧   OCR 标注 %d 条   L3 正常集 %d 张"
          % (n_masked, len(views), len(ocr_rows),
             len(list((root / "normal").glob("*.jpg")))))
    if a.preview:
        print("核对图 %s/check/ —— **务必人眼抽查几张**：掩膜错位、类别号错位"
              "在数字上完全看不出来" % root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
