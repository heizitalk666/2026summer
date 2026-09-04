#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 N 次独立标定的结果画成分布图，并落一份 CSV。

    # 先跑 N 次标定（每次换一个种子，产物丢临时目录）
    for s in $(seq 1 30); do
        python -m patrol.tools.calibrate --seed $s --out /tmp/cal/s$s >/tmp/cal/s$s.log
    done
    python deliverables/组长-系统/make_figures.py --logs '/tmp/cal/s*.log'

**为什么要画分布而不是报一个数。** 重复性这一项是压着限值的，单跑一次报出来的
数在 0.22–0.35 % FS 之间跳——报哪个都像在挑数。把 N 次的分布画出来，"系统性地
压在限值上"这个结论就不依赖某一次的运气，而且**图上一眼能看出限值线在分布的
哪个位置**。这张图是要放进 PPT 精度页的，它承认指标没达标。

脚本从标定的终端输出里解析三项指标，不重算——**指标的真值在 `calibrate` 里，
这里只做汇总与作图**，避免出现"图上的数和记录里的数对不上"这种最难查的问题。
"""
from __future__ import annotations

import argparse
import csv
import glob
import re
import statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent

PAT = re.compile(r"^(线性度|重复性|基本误差)\s+([\d.]+) % FS\s+\(限值 ([\d.]+)\)\s+(合格|超差)",
                 re.M)
LOW = re.compile(r"低置信度读数\s+(\d+) / (\d+) 次")

LABELS = {"线性度": "Linearity", "重复性": "Repeatability", "基本误差": "Basic error"}


def parse(paths: list[str]) -> tuple[list[dict], list[tuple[int, int]]]:
    rows, lows = [], []
    for p in sorted(paths):
        txt = Path(p).read_text(encoding="utf-8", errors="replace")
        rec = {"run": Path(p).stem}
        for name, val, lim, verdict in PAT.findall(txt):
            rec[name] = float(val)
            rec[name + "_限值"] = float(lim)
            rec[name + "_判定"] = verdict
        if "重复性" in rec:
            rows.append(rec)
        m = LOW.search(txt)
        if m:
            lows.append((int(m.group(1)), int(m.group(2))))
    return rows, lows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="标定结果分布图")
    ap.add_argument("--logs", required=True, help="标定输出日志的 glob，如 '/tmp/cal/s*.log'")
    ap.add_argument("--out", default=str(HERE))
    a = ap.parse_args(argv)

    rows, lows = parse(glob.glob(a.logs))
    if not rows:
        print("没解析到任何标定结果，检查 --logs")
        return 1
    out = Path(a.out)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    (out / "artifacts").mkdir(parents=True, exist_ok=True)

    fields = ["run", "线性度", "重复性", "基本误差"]
    with (out / "artifacts" / "repeatability_seeds.csv").open(
            "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    metrics = ["线性度", "重复性", "基本误差"]
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 4.0))
    for ax, name in zip(axes, metrics):
        vs = [r[name] for r in rows if name in r]
        lim = next(r[name + "_限值"] for r in rows if name + "_限值" in r)
        ax.hist(vs, bins=12, color="#4C72B0", edgecolor="white")
        ax.axvline(lim, color="#C44E52", lw=2,
                   label="limit %.2f %%FS" % lim)
        ax.axvline(st.median(vs), color="#55A868", lw=2, ls="--",
                   label="median %.3f" % st.median(vs))
        bad = sum(1 for v in vs if v > lim)
        ax.set_title("%s  (%s)\n%d/%d over limit" % (LABELS[name], name, bad, len(vs)))
        ax.set_xlabel("%% FS")
        ax.set_ylabel("runs")
        ax.legend(fontsize=8)
    fig.suptitle("Calibration metrics over %d independent runs　N 次独立标定的指标分布"
                 % len(rows))
    fig.tight_layout()
    fig.savefig(out / "figures" / "repeatability_seeds.png", dpi=150)

    print("N = %d 次独立标定" % len(rows))
    print("%-10s %6s %7s %7s %7s  超限" % ("指标", "限值", "最小", "中位", "最大"))
    for name in metrics:
        vs = [r[name] for r in rows if name in r]
        lim = next(r[name + "_限值"] for r in rows if name + "_限值" in r)
        print("%-10s %6.2f %7.3f %7.3f %7.3f  %d/%d"
              % (name, lim, min(vs), st.median(vs), max(vs),
                 sum(1 for v in vs if v > lim), len(vs)))
    if lows:
        n_low = sum(a_ for a_, _ in lows)
        n_all = sum(b for _, b in lows)
        print("\n算法自报低置信度读数：%d 次，出现在 %d 轮里（该轮共 %d 次读数）"
              % (n_low, len(lows), n_all))
    else:
        print("\n本批未出现算法自报低置信度的读数")
    print("\n已写出 figures/repeatability_seeds.png 与 artifacts/repeatability_seeds.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
