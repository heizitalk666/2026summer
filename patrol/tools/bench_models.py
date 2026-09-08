#!/usr/bin/env python3
"""四路模型的横向对比：用数据决定每一路该用哪个实现。

    python -m patrol.tools.bench_models                     # 全跑
    python -m patrol.tools.bench_models --only reading      # 只比读数
    python -m patrol.tools.bench_models --seg-weights training/runs/seg/pixel.npz

**这个工具的存在本身就是一个立场：多模型协同不是"模型越多越好"。**

每加一路模型都要付出代价——推理时间、内存、一个会失效的新组件、以及一条
新的"两路说法不一致时怎么办"的规则。所以每一路都得回答同一个问题：
*它到底把什么指标变好了多少*。答不上来的那一路就该关掉。

三张表：

  读数    几何法 vs 分割级联，比读数误差（% FS）与耗时，按像素密度分档
  OCR     互证通路的判对率，按像素密度分档——顺带量出"字到底多大才读得出"
  耗时    每一路的单次推理耗时，用来核对 30 Hz 巡航 / 3 s 复核的预算

**按像素密度分档是刻意的。**整套方案的立论就是"密度不够就读不准，所以要
停车变焦"。一个不分档的平均值会把这条立论抹掉——而它恰恰是本课题最该被
数据支撑的一句话。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from patrol.common.config import Config
from patrol.perception.reading.nameplate import cross_check_dial, parse_dial_text
from patrol.perception.reading.pointer import read_pointer_gauge
from patrol.scene.gauges import render_pointer_gauge

#: 分档边界（像素密度）。120 是判据线，两侧各留两档看趋势。
BANDS = [(30, 60), (60, 90), (90, 120), (120, 180), (180, 320)]
PRIORS = {"kind": "POINTER_GAUGE", "unit": "MPa", "range_min": 0.0,
          "range_max": 1.6, "sweep_deg": 270.0, "zero_offset_deg": -135.0,
          "normal_band": [0.4, 1.2], "major_ticks": 27}


def make_dial(px: int, value: float, *, tilt_ratio: float = 1.0,
              blur: float = 0.0, noise: float = 0.0,
              rng: np.random.Generator | None = None) -> np.ndarray:
    """画一块表，放到灰底上，再按需要加透视压扁、模糊和噪声。

    压扁模拟斜看（真机上表计很少正对），模糊模拟云台残余抖动，
    噪声模拟低照度增益——这三样都是实机上一定有、而干净渲染图里没有的。
    """
    rng = rng or np.random.default_rng(0)
    src = render_pointer_gauge(
        512, value=value, range_min=PRIORS["range_min"],
        range_max=PRIORS["range_max"], sweep_deg=PRIORS["sweep_deg"],
        zero_offset_deg=PRIORS["zero_offset_deg"],
        major_ticks=int(PRIORS["major_ticks"]), unit=str(PRIORS["unit"]),
        normal_band=tuple(PRIORS["normal_band"]))
    h = max(8, int(round(px * tilt_ratio)))
    small = cv2.resize(src, (px, h), interpolation=cv2.INTER_AREA)
    pad = max(24, px // 4)
    img = np.full((h + 2 * pad, px + 2 * pad, 3), 150, np.uint8)
    img[pad:pad + h, pad:pad + px] = small
    if blur > 0:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    if noise > 0:
        img = np.clip(img.astype(np.float32)
                      + rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
    return img, (pad, pad, pad + px, pad + h)


def _err_stats(errs, times):
    """一档读数的统计：读出率、误差 P50/P90/P95、耗时。

    P90 而不是中位数才是该看的量——像素密度买到的是"不出大错"，不是"典型
    误差更小"（见 deliverables/乙-分割/补齐清单.md 第 3.2 节）。
    """
    n = len(times)
    if n == 0:
        return {"n": 0, "readout": None, "p50": None, "p90": None,
                "p95": None, "latency_ms": None}
    e = np.asarray(errs, np.float64)
    return {
        "n": n,
        "readout": float(len(e) / n),              # 读得出的样本占比
        "p50": float(np.median(e)) if len(e) else None,
        "p90": float(np.percentile(e, 90)) if len(e) else None,
        "p95": float(np.percentile(e, 95)) if len(e) else None,
        "latency_ms": float(np.median(np.asarray(times))),
    }


def bench_reading(segmenter, *, n=24, seed=0, json_path=None) -> dict | None:
    rng = np.random.default_rng(seed)
    span = PRIORS["range_max"] - PRIORS["range_min"]
    seg_name = segmenter.model_info().get("name", "seg") if segmenter else None
    rows = []
    print("\n读数：几何法 vs 分割级联（n=%d）" % n)
    for lo, hi in BANDS:
        errs = {"geo": [], "seg": []}
        times = {"geo": [], "seg": []}
        for _ in range(n):
            px = int(rng.integers(lo, hi))
            v = float(rng.uniform(PRIORS["range_min"] + 0.05 * span,
                                  PRIORS["range_max"] - 0.05 * span))
            img, box = make_dial(px, v, tilt_ratio=float(rng.uniform(0.82, 1.0)),
                                 blur=float(rng.uniform(0.0, 0.8)),
                                 noise=float(rng.uniform(0.0, 3.0)), rng=rng)
            for tag, seg in (("geo", None), ("seg", segmenter)):
                if seg is None and tag == "seg":
                    continue
                t0 = time.perf_counter()
                r = read_pointer_gauge(img, box, PRIORS, segmenter=seg)
                times[tag].append((time.perf_counter() - t0) * 1000)
                if r.ok:
                    errs[tag].append(abs(r.value - v) / span * 100.0)

        band = {"lo": lo, "hi": hi, "n": n}
        for tag in ("geo", "seg"):
            if tag == "seg" and segmenter is None:
                continue
            st = _err_stats(errs[tag], times[tag])
            st["method"] = "geometric" if tag == "geo" else seg_name
            st["raw_errors"] = errs[tag]
            band[tag] = st
        rows.append(band)

    def _v(x):
        return "   —" if x is None else "%6.2f" % x

    print("  %-11s %-6s %-14s %6s %7s %7s %7s %7s" %
          ("像素密度", "样本", "方法", "读出率", "P50", "P90", "P95", "耗时ms"))
    for b in rows:
        print("  %-11s %-6d" % ("%d–%d px" % (b["lo"], b["hi"]), b["n"]))
        for tag, mname in (("geo", "几何"), ("seg", "级联")):
            if tag not in b:
                continue
            s = b[tag]
            print("  %-11s %-6s %-14s %6s %s %s %s %7.1f" %
                  ("", "", mname,
                   ("%.1f%%" % (100 * s["readout"])
                    if s["readout"] is not None else "   —"),
                   _v(s["p50"]), _v(s["p90"]), _v(s["p95"]), s["latency_ms"]))
    print("  （误差 %FS；P90 是各档读得出的样本里误差的第 90 百分位；"
          "判据线 120 px 对应 0.5 %FS 的设计目标）")

    payload = {
        "seed": seed, "n": n, "span": span,
        "seg_name": seg_name,
        "seg_weights": (segmenter.path if hasattr(segmenter, "path") else None)
        if segmenter else None,
        "bands": rows,
    }
    if json_path:
        p = Path(json_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        print("  结果写到 %s" % p)
    return payload


def bench_ocr(ocr, *, n=12, seed=1) -> None:
    if ocr is None or not ocr.available:
        print("\nOCR：引擎未启用，跳过")
        return
    rng = np.random.default_rng(seed)
    print("\nOCR 互证：判对率与耗时（这一条量的是：字要多大才读得出）")
    print("  %-11s %-7s %9s %9s %9s %8s" %
          ("像素密度", "样本", "判一致", "证据不足", "误判冲突", "耗时ms"))
    for lo, hi in BANDS:
        agree = insuf = conflict = 0
        ts = []
        for _ in range(n):
            px = int(rng.integers(lo, hi))
            img, box = make_dial(px, float(rng.uniform(0.2, 1.4)), rng=rng)
            t0 = time.perf_counter()
            lines = ocr.read(img, box)
            ts.append((time.perf_counter() - t0) * 1000)
            c = cross_check_dial(parse_dial_text(lines), PRIORS)
            if c.agree is True:
                agree += 1
            elif c.agree is False:
                conflict += 1
            else:
                insuf += 1
        print("  %-11s %-7d %9d %9d %9d %8.0f"
              % ("%d–%d px" % (lo, hi), n, agree, insuf, conflict,
                 float(np.median(ts)) if ts else 0.0))
    print("  误判冲突这一列必须接近 0：它是好表被自己的互证通路判成标定错配，")
    print("  代价是白烧一次复核预算并推一条工单给人——比漏判贵得多。")


def bench_latency(cfg, segmenter, ocr, *, n=10) -> None:
    from patrol.perception.anomaly import build_anomaly
    from patrol.perception.detector.base import build_detector

    print("\n单次推理耗时（中位数，用来核对巡航 30 Hz / 复核 3 s 的预算）")
    img, box = make_dial(160, 0.8)
    rows = []

    det = build_detector(cfg, None)
    t = []
    for _ in range(n):
        t0 = time.perf_counter()
        det.infer(np.zeros((1080, 1920, 3), np.uint8), conf_threshold=0.25)
        t.append((time.perf_counter() - t0) * 1000)
    rows.append(("L1 检测", det.model_info().get("name", "?"), np.median(t)))

    t = []
    for _ in range(n):
        t0 = time.perf_counter()
        read_pointer_gauge(img, box, PRIORS)
        t.append((time.perf_counter() - t0) * 1000)
    rows.append(("L2 读数 几何", "geometric", np.median(t)))

    if segmenter is not None:
        t = []
        for _ in range(n):
            t0 = time.perf_counter()
            read_pointer_gauge(img, box, PRIORS, segmenter=segmenter)
            t.append((time.perf_counter() - t0) * 1000)
        rows.append(("L2 读数 级联", segmenter.model_info().get("name", "?"),
                     np.median(t)))

    if ocr is not None and ocr.available:
        t = []
        for _ in range(n):
            t0 = time.perf_counter()
            ocr.read(img, box)
            t.append((time.perf_counter() - t0) * 1000)
        rows.append(("L2' OCR", ocr.model_info().get("name", "?"), np.median(t)))

    an = build_anomaly(cfg)
    if an is not None:
        t = []
        for _ in range(n):
            t0 = time.perf_counter()
            an.score(img, box)
            t.append((time.perf_counter() - t0) * 1000)
        rows.append(("L3 异常", type(an).__name__, np.median(t)))

    print("  %-16s %-24s %8s" % ("环节", "实现", "耗时ms"))
    for name, impl, ms in rows:
        print("  %-16s %-24s %8.1f" % (name, impl, ms))
    heavy = sum(ms for name, _i, ms in rows if not name.startswith("L1"))
    print("  复核期重模型合计 %.0f ms（预算 3000 ms）；"
          "巡航期只跑 L1 %.0f ms（30 Hz 需 < 33 ms）"
          % (heavy, rows[0][2]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="四路模型横向对比")
    ap.add_argument("--config", default=None)
    ap.add_argument("--only", choices=["reading", "ocr", "latency"], default=None)
    ap.add_argument("--seg-weights", default=None,
                    help="分割权重；不给就只跑几何法那一列")
    ap.add_argument("--n", type=int, default=24, help="每档样本数")
    ap.add_argument("--json", default=None,
                    help="把读数的原始误差与 P50/P90/P95 写到这个 JSON")
    a = ap.parse_args(argv)

    cfg = Config.load(a.config)
    segmenter = None
    if a.seg_weights:
        w = str(a.seg_weights)
        try:
            if w.endswith(".onnx"):
                from patrol.perception.segment.onnx_seg import OnnxSegmenter
                segmenter = OnnxSegmenter(cfg, weights=w)
            else:
                from patrol.perception.segment.pixel import NpzSegmenter
                segmenter = NpzSegmenter(cfg, weights=w)
            print("分割模型：%s" % segmenter.model_info())
        except Exception as e:                                 # noqa: BLE001
            print("分割权重加载失败，只比几何法：%s" % e)
    from patrol.perception.ocr.base import build_ocr
    ocr = build_ocr(cfg)

    if a.only in (None, "reading"):
        bench_reading(segmenter, n=a.n, json_path=a.json)
    if a.only in (None, "ocr"):
        bench_ocr(ocr, n=max(6, a.n // 2))
    if a.only in (None, "latency"):
        bench_latency(cfg, segmenter, ocr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
