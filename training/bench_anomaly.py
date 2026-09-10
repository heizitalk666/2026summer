#!/usr/bin/env python3
"""L3 异常检测的同批评测。`python -m training.bench_anomaly`

    python -m training.bench_anomaly --train-data training/datasets/normal_patches \
        --out deliverables/丙-异常/figures

流程:生成评测集(另种子的正常裁片 + 场景渲染的异常裁片)→ 三种方法在同一批
样本上打分 → 误报/漏报表 + 两张图(分数分布、误报漏报对比)。

**训练与评测的数据卫生**:训练只吃 gen_synthetic 的 `normal/` 子目录——完整
帧里可能含 FOREIGN_OBJECT 异常目标,核对图带标注叠加,混进正常分布等于教
模型"异常也是正常"(实测 EfficientAD 因此分数倒挂)。评测集用另种子生成,
异常裁片直接渲染场景里的 FOREIGN_OBJECT,按渲染器真值 bbox 裁出。

Windows 中文路径注意:所有图读写都走 imdecode/imencode,cv2.imread/imwrite
对含中文的绝对路径会静默失败。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import cv2
import numpy as np

from patrol.perception.anomaly import (EfficientADAnomaly, PadimAnomaly,
                                       StatisticalAnomaly)
from patrol.tools.textdraw import draw_text

THR = 0.55
CMP = REPO / "out/l3_eval"


def imread_safe(p: Path) -> np.ndarray | None:
    if not p.exists():
        return None
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)


def imwrite_safe(p: Path, img: np.ndarray) -> bool:
    ok, buf = cv2.imencode(p.suffix or ".jpg", img,
                           [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        return False
    p.write_bytes(buf.tobytes())
    return True


# ---------------------------------------------------------------- 评测集
def make_testset(out: Path, n_frames: int = 120) -> dict:
    norm_dir, anom_dir = out / "normal", out / "anomaly"
    norm_dir.mkdir(parents=True, exist_ok=True)
    anom_dir.mkdir(parents=True, exist_ok=True)

    # 正常裁片:另种子的 gen_synthetic(必须传相对路径,gen 内部用 cv2.imwrite)
    from training.gen_synthetic import main as gen
    tmp = Path("out/l3_eval/_synth")
    if tmp.exists():
        import shutil
        shutil.rmtree(tmp)
    gen(["--n", str(n_frames), "--out", str(tmp), "--seed", "8"])
    n_norm = 0
    for p in (tmp / "normal").glob("*.jpg"):
        img = imread_safe(p)
        if img is None or min(img.shape[:2]) < 24:
            continue
        if imwrite_safe(norm_dir / p.name, img):
            n_norm += 1

    # 异常裁片:相机扫 TGT-07 一组视角,按真值 bbox 裁
    from patrol.common.config import Config
    from patrol.scene.render import RenderOptions, SceneRenderer
    from patrol.scene.world import World
    cfg = Config.load()
    world = World(cfg)
    r = SceneRenderer(world, RenderOptions(
        width=1920, height=1080,
        hfov_at_1x_deg=float(cfg.get("optics.hfov_at_1x_deg")),
        simulate_4k_crop=True,
        source_width=int(cfg.get("stub.ptz.source_width", 3840))), seed=3)
    n_anom = 0
    for zoom in (1.0, 1.6, 2.2, 3.0):
        for pan in np.linspace(78.0, 102.0, 6):
            for dx, tilt in ((0.0, 1.0), (0.3, 2.5), (-0.2, 0.0)):
                img, meta = r.render(pose_xy_yaw=(14.0 + dx, -3.18, 0.0),
                                     pan_deg=float(pan), tilt_deg=float(tilt),
                                     zoom=float(zoom), speed_mps=0.0)
                for m in meta:
                    if not m["anomalous"]:
                        continue
                    x1, y1, x2, y2 = [int(round(v)) for v in m["bbox"]]
                    crop = img[max(0, y1):y2, max(0, x1):x2]
                    if crop.size and min(crop.shape[:2]) >= 24 \
                            and n_anom < 60:
                        if imwrite_safe(anom_dir / ("anom_%03d.jpg" % n_anom),
                                        crop):
                            n_anom += 1
                if n_anom >= 60:
                    break
            if n_anom >= 60:
                break
        if n_anom >= 60:
            break

    # 第二类异常:合成贴片。训练与场景里都没出现过的新形状(seed 100 起,
    # 与 TGT-07 的 seed 不同),贴到真实柜面背景上——评测"未知"异常就该
    # 用模型没见过的东西。
    from patrol.scene import gauges
    rng = np.random.default_rng(42)
    for i in range(60):
        # x=0.5 这一段的柜面没有任何目标(最近的目标在 x=4),背景是干净的
        bg, meta = r.render(pose_xy_yaw=(0.5, -3.18, 0.0), pan_deg=90.0,
                            tilt_deg=2.0, zoom=1.5, speed_mps=0.0)
        h, w = bg.shape[:2]
        y0 = rng.integers(150, h - 350)
        x0 = rng.integers(200, w - 350)
        patch = bg[y0:y0 + 256, x0:x0 + 256].copy()
        size = int(rng.integers(40, 110))
        obj = gauges.render_anomaly_object(size, seed=100 + i)
        ox = int(rng.integers(10, 256 - size - 10))
        oy = int(rng.integers(10, 256 - size - 10))
        patch[oy:oy + size, ox:ox + size] = obj
        noise = rng.normal(0, 2.5, patch.shape)
        patch = np.clip(patch.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        if imwrite_safe(anom_dir / ("anom_%03d.jpg" % n_anom), patch):
            n_anom += 1
    return {"normal": n_norm, "anomaly": n_anom}


# ---------------------------------------------------------------- 打分
def score_dir(det, d: Path) -> list[float]:
    out = []
    for p in sorted(d.glob("*.jpg")):
        img = imread_safe(p)
        if img is None:
            continue
        r = det.score(img, (0.0, 0.0, float(img.shape[1]), float(img.shape[0])))
        out.append(r.anomaly_score)
    return out


def _hist(img, xs, y0, h, x0, x1, w, color, label, thr):
    if not xs:
        return
    hist, _ = np.histogram(xs, bins=40, range=(0.0, 1.0))
    hist = hist / max(1, hist.max())
    bw = w / 40
    for i, v in enumerate(hist):
        if v <= 0:
            continue
        cv2.rectangle(img, (int(x0 + i * bw), int(y0 + h - v * h)),
                      (int(x0 + (i + 1) * bw - 1), int(y0 + h)), color, -1)
    cv2.line(img, (int(x0 + thr * w), int(y0)), (int(x0 + thr * w), int(y0 + h)),
             (60, 60, 200), 1, cv2.LINE_AA)
    draw_text(img, label, (int(x0), int(y0) - 22), size=14, color=color)


def plot_dist(groups, out_png: Path) -> None:
    W, H = 1000, 260 + 240 * len(groups)
    img = np.full((H, W, 3), 252, np.uint8)
    draw_text(img, "异常分分布(绿=正常裁片,蓝=异常裁片,竖线=阈值 0.55)",
              (30, 24), size=22, color=(40, 40, 40))
    for i, (name, norm, anom) in enumerate(groups):
        y0 = 70 + i * 240
        _hist(img, norm, y0, 190, 40, 660, 620, (60, 150, 60),
              name + " · 正常", THR)
        _hist(img, anom, y0, 190, 40, 660, 620, (60, 60, 200),
              name + " · 异常(叠画)", THR)
    draw_text(img, "分数 →", (690, H - 60), size=14, color=(110, 110, 110))
    imwrite_safe(out_png, img)


def plot_compare(report: dict, out_png: Path) -> None:
    W, H = 1000, 640
    img = np.full((H, W, 3), 252, np.uint8)
    draw_text(img, "同批评测上的误报率与漏报率(阈值 0.55)", (30, 24),
              size=22, color=(40, 40, 40))
    names = ["统计法(零权重)", "EfficientAD(简化蒸馏)", "PaDiM(对角)",
             "PaDiM(全协方差)"]
    keys = ("statistical", "efficientad", "padim", "padim_cov")
    x0, wmax, ymax = 90, 760, 1.0
    for i, (name, k) in enumerate(zip(names, keys)):
        y = 70 + i * 140
        draw_text(img, name, (30, y + 4), size=18, color=(40, 40, 40))
        for j, (v, color, label) in enumerate(
                ((report[k]["fpr"], (60, 150, 60), "误报率"),
                 (report[k]["fnr"], (60, 60, 200), "漏报率"))):
            yy = y + 24 + j * 46
            x1 = x0 + int(v / ymax * wmax)
            cv2.rectangle(img, (x0, yy), (x1, yy + 34), color, -1)
            cv2.putText(img, "%.1f %%" % (v * 100), (x1 + 8, yy + 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
            draw_text(img, label, (x0 - 8, yy + 6), size=13,
                      color=(90, 140, 90) if j == 0 else (90, 90, 150))
    draw_text(img, "阈值 0.55;L3 输出只进人工复核队列,不直接告警——"
                   "用误报换看得见是划算的", (30, H - 40), size=15,
              color=(100, 100, 100))
    imwrite_safe(out_png, img)


def plot_roc(sweeps: dict, out_png: Path) -> None:
    """TPR-FPR 曲线(阈值扫描),一张图回答"误报能不能调低、代价多大"。"""
    W, H = 900, 620
    img = np.full((H, W, 3), 252, np.uint8)
    draw_text(img, "ROC(阈值 0→1 扫描,同批评测集)", (30, 24), size=22,
              color=(40, 40, 40))
    ml, mr, mt, mb = 80, 60, 60, 60
    x0, y0 = ml, H - mb
    w, h = W - ml - mr, H - mt - mb

    def px(v): return int(x0 + v * w)
    def py(v): return int(y0 - v * h)
    cv2.rectangle(img, (x0, y0 - h), (x0 + w, y0), (215, 215, 215), 1)
    for i in range(6):
        v = i / 5
        cv2.line(img, (px(v), y0), (px(v), y0 - h), (232, 232, 232), 1)
        cv2.putText(img, "%.1f" % v, (px(v) - 10, y0 + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (110, 110, 110), 1, cv2.LINE_AA)
        cv2.line(img, (x0, py(v)), (x0 + w, py(v)), (232, 232, 232), 1)
        cv2.putText(img, "%.1f" % v, (x0 - 34, py(v) + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (110, 110, 110), 1, cv2.LINE_AA)
    cv2.line(img, (x0, py(0)), (px(1), py(1)), (170, 170, 170), 1, cv2.LINE_AA)
    colors = {"statistical": (60, 150, 60), "efficientad": (150, 60, 160),
              "padim": (60, 60, 200), "padim_cov": (200, 120, 40)}
    labels = {"statistical": "统计法", "efficientad": "EfficientAD",
              "padim": "PaDiM-对角", "padim_cov": "PaDiM-全协方差"}
    for i, (key, pts) in enumerate(sweeps.items()):
        c = colors[key]
        for a, b in zip(pts, pts[1:]):
            cv2.line(img, (px(a[0]), py(a[1])), (px(b[0]), py(b[1])), c, 2,
                     cv2.LINE_AA)
        cv2.circle(img, (px(pts[-1][0]), py(pts[-1][1])), 4, c, -1, cv2.LINE_AA)
        draw_text(img, labels[key], (x0 + 10 + i * 150, mt - 20 + (i % 2) * 0),
                  size=16, color=c)
    draw_text(img, "误报率 FPR →", (x0 + w - 130, y0 + 6), size=14,
              color=(110, 110, 110))
    draw_text(img, "漏报率=1-TPR", (12, mt + 8), size=14, color=(110, 110, 110))
    ok, buf = cv2.imencode(".png", img)
    if ok:
        out_png.write_bytes(buf.tobytes())


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="L3 同批评测")
    ap.add_argument("--train-data", default="training/datasets/normal_patches")
    ap.add_argument("--baseline", default="training/runs/anomaly/baseline.json")
    ap.add_argument("--padim", default="training/runs/anomaly/padim.pt")
    ap.add_argument("--padim-cov",
                    default="training/runs/anomaly/padim_cov.pt")
    ap.add_argument("--efficientad",
                    default="training/runs/anomaly/efficientad.pt")
    ap.add_argument("--out", default="deliverables/丙-异常/figures")
    a = ap.parse_args()

    train_data = Path(a.train_data)
    out_figs = Path(a.out)
    out_figs.mkdir(parents=True, exist_ok=True)

    print("生成评测集 …")
    counts = make_testset(CMP)
    print("  正常 %d 张 | 异常 %d 张" % (counts["normal"], counts["anomaly"]))

    stat = StatisticalAnomaly(threshold=THR, warmup=0)
    stat.load_baseline(json.loads(Path(a.baseline).read_text(encoding="utf-8")))
    methods = [
        ("statistical", stat, a.baseline, "stat_lab_grad"),
        ("efficientad", EfficientADAnomaly(a.efficientad, threshold=THR,
                                           device="cpu"), a.efficientad,
         "efficientad_s"),
        ("padim", PadimAnomaly(a.padim, threshold=THR, device="cpu"),
         a.padim, "padim_s"),
        ("padim_cov", PadimAnomaly(a.padim_cov, threshold=THR, device="cpu"),
         a.padim_cov, "padim_cov_s"),
    ]

    report = {"threshold": THR,
              "train_normal_patches":
                  len(list((train_data / "normal").glob("*.jpg"))),
              "test_normal": counts["normal"], "test_anomaly": counts["anomaly"],
              "test_anomaly_note": "场景 TGT-07 裁片 60 + 未见形状贴片 60"}
    groups = []
    sweeps: dict = {}
    all_norm: dict = {}
    for key, det, wpath, mname in methods:
        norm = score_dir(det, CMP / "normal")
        anom = score_dir(det, CMP / "anomaly")
        fp = sum(1 for s in norm if s > THR)
        fn = sum(1 for s in anom if s <= THR)
        report[key] = {
            "model": mname, "weights": wpath,
            "n_normal": len(norm), "n_anomaly": len(anom), "fp": fp, "fn": fn,
            "fpr": round(fp / len(norm), 4) if norm else None,
            "fnr": round(fn / len(anom), 4) if anom else None,
            "mean_score_normal": round(float(np.mean(norm)), 4),
            "mean_score_anomaly": round(float(np.mean(anom)), 4)}
        groups.append((mname, norm, anom))
        all_norm[key] = norm
        sweeps[key] = []
        for t in np.arange(0.0, 1.01, 0.05):
            fpr = sum(1 for s in norm if s > t) / len(norm)
            tpr = sum(1 for s in anom if s > t) / len(anom)
            sweeps[key].append((round(fpr, 4), round(tpr, 4)))
        report[key]["roc"] = sweeps[key]
    # 误报归因:PaDiM 分数最高的 5 张正常裁片是哪些
    pnorm = sorted(zip((CMP / "normal").glob("*.jpg"), all_norm["padim"]),
                   key=lambda t: -t[1])[:5]
    report["padim_top_false_positives"] = [
        {"file": p.name, "score": round(s, 4)} for p, s in pnorm]
    (CMP / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    plot_dist(groups, out_figs / "score_dist.png")
    plot_compare(report, out_figs / "compare.png")
    plot_roc(sweeps, out_figs / "roc.png")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("图已写出 → %s/" % out_figs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
