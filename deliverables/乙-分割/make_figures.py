#!/usr/bin/env python3
"""生成乙（L2 分割）交付的三张图。跑一遍即可复现：

    python deliverables/乙-分割/make_figures.py

数字全部来自实测，不是手编的：
  - iou_compare / mask_check 用的 IoU 与核对图来自训练产物；
  - reading_error 读 artifacts/reading_bench_{numpy,unet}.json，那是
    `bench_models --only reading --n 200 --json ...` 的落盘结果（几何法 vs
    numpy 逻辑回归 vs U-Net 的逐档误差）。

跑这个脚本需要 matplotlib 与 opencv；中文字体按 雅黑 → 黑体 → 思源 → 文泉驿
的顺序回退，都没有才落到 DejaVu Sans（此时中文显示为方块，图不可用）。
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                   "Noto Sans CJK SC", "Source Han Sans SC",
                                   "WenQuanYi Zen Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

HERE = Path(__file__).resolve().parent
FIG = HERE / "figures"
REPO = HERE.parent.parent

#: 读入门槛。fusion.py 的 DENSITY_FLOOR_FRAC=0.80，判据线 120 px 的 80 %。
READ_FLOOR_PX = 96.0
#: 分档里从第几档起算"系统实际读数"（90–120 px 起跨过 96 px 门槛）。
READ_START = 2

#: 三类方法的配色。顺序固定：几何法（蓝）、numpy 基线（灰）、U-Net（绿）。
C_GEO = "#2f6fb2"
C_NPY = "#8a8f98"
C_UNET = "#2e7d32"


def mask_check():
    """两张核对图并排：合成掩膜（gen_synthetic --preview）与 PaddleX 真实标注
    叠加（--from-paddlex 的 check/）。掩膜错位在数字上看不出来，只能画出来看。

    图例颜色必须和图上叠加色一致：draw_check/_seg_check 里写的是 BGR，
    这里画图已经转回 RGB，所以图例直接给转好的 RGB，别再写 BGR 常量。
    """
    synth = REPO / "training/datasets/synth/check/000003.jpg"
    paddx = REPO / "training/datasets/seg_paddlex/check/paddlex_105.jpg"
    a = cv2.imread(str(synth))
    b = cv2.imread(str(paddx))
    if a is None or b is None:
        raise SystemExit("核对图缺失：先跑 gen_synthetic 与 prepare_dataset --from-paddlex")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, img, title in (
            (axes[0], a, "合成掩膜（gen_synthetic --preview）"),
            (axes[1], b, "PaddleX 真实标注（--from-paddlex check）")):
        ax.imshow(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        ax.set_title(title, fontsize=11)
        ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle("分割标注叠加核对：针=红、刻度=蓝、盘面=绿（灰=忽略区）", fontsize=12)
    # 图例色块。
    #
    # **这里必须和上面的 imshow 一样做 BGR→RGB。**prepare_dataset._seg_check 与
    # gen_synthetic.draw_check 里的常量是 OpenCV 的 BGR，而 matplotlib 的
    # color= 收的是 RGB。图转了、图例没转的话，图例会把针说成蓝、刻度说成橙，
    # 而图上明明是针红刻度蓝——看图的人会据此判定"针和刻度这两类映射反了"，
    # 从而否掉一个其实正确的转换结果。图例自己拆自己的台，比没有图例更糟。
    from matplotlib.patches import Patch
    def _rgb(bgr):
        b, g, r = bgr
        return (r / 255, g / 255, b / 255)
    handles = [Patch(color=_rgb((60, 60, 235)), label="needle 针"),
               Patch(color=_rgb((200, 160, 60)), label="ticks 刻度"),
               Patch(color=_rgb((90, 140, 60)), label="face 盘面")]
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=10)
    fig.tight_layout(rect=[0, 0.05, 1, 0.94])
    fig.savefig(FIG / "mask_check.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("mask_check.png")


def iou_compare():
    """针的 IoU 对比：numpy 基线（合成 / 合成+PaddleX）vs U-Net。"""
    # 横轴标签两行、字数不等，6.4 英寸画布上会互相压字，加宽到 7.6 英寸并缩短文案
    labels = ["numpy 基线\n仅合成集", "numpy 基线\n合成 + PaddleX",
              "U-Net\n合成 + PaddleX"]
    vals = [0.251, 0.384, 0.778]
    colors = ["#9aa5b1", "#4a90d9", "#2e7d32"]

    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    bars = ax.bar(labels, vals, color=colors, width=0.55)
    ax.set_ylabel("针的 IoU（验证集，QUOTA 重平衡采样）", fontsize=11)
    ax.set_ylim(0, 1.0)
    ax.set_title("针的分割 IoU：U-Net 明显优于 numpy 基线", fontsize=12)
    ax.tick_params(axis="x", labelsize=10)
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.02, "%.3f" % v,
                ha="center", fontsize=11, fontweight="bold")
    ax.axhline(0.182, color="crimson", linestyle="--", linewidth=1)
    # 注记贴着虚线放会压在第三根柱子上，改放左上角空白处，用文字说明虚线含义
    ax.text(0.02, 0.95, "红色虚线：旧基线 0.182（文档记录值）", color="crimson",
            fontsize=9.5, ha="left", va="top", transform=ax.transAxes)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG / "iou_compare.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("iou_compare.png")


def _load_bench(name: str) -> dict:
    p = HERE / "artifacts" / name
    if not p.exists():
        raise SystemExit("缺 %s：先跑 "
                         "`python -m patrol.tools.bench_models --only reading "
                         "--n 200 --json ...`" % p)
    return json.loads(p.read_text(encoding="utf-8"))


def _p90_ci(errors, n_boot=2000, seed=0):
    """P90 的 bootstrap 95% 置信区间（误差棒用）。"""
    e = np.asarray(errors, np.float64)
    if len(e) < 20:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(e), (n_boot, len(e)))
    boots = np.percentile(e[idx], 90, axis=1)
    return (float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5)))


def reading_error():
    """读数误差按像素密度分档：几何法 vs numpy 基线 vs U-Net（核心图）。

    数据来自 bench_models --only reading --n 200，纵轴改画 P90（第 90 百分位）
    而不是中位数——中位数几乎不随密度动，动的是尾部，也就是"密度买的是
    不出大错"。30–60 / 60–90 两档标灰：密度低于 96 px（120×0.8），fusion
    拒绝下任何读数结论，系统根本不在那里读数。numpy 基线的低密度两档不画：
    30–60 px 的 P90 达 1.44，远超坐标，而且那两档系统本就不下结论。
    """
    unet = _load_bench("reading_bench_unet.json")
    npy = _load_bench("reading_bench_numpy.json")
    # 两份跑同一个 seed，geo 列完全一致，取 unet 那份即可。
    bands = unet["bands"]

    labels = ["%d–%d" % (b["lo"], b["hi"]) for b in bands]
    x = np.arange(len(bands))

    def cols(d, tag):
        return [b[tag] for b in d["bands"]]

    geo = cols(unet, "geo")
    npy_seg = cols(npy, "seg")
    unet_seg = cols(unet, "seg")

    def p90(c):
        return np.array([r["p90"] for r in c], dtype=float)

    geo_p90, npy_p90, unet_p90 = p90(geo), p90(npy_seg), p90(unet_seg)

    # numpy 基线只画 96 px 以上的档；低密度两档标灰且数值越界，不画。
    npy_plot = npy_p90.copy()
    npy_plot[:READ_START] = np.nan

    def yerr(c, p):
        lo, hi = [], []
        for r, v in zip(c, p):
            a, b = _p90_ci(r["raw_errors"])
            if np.isnan(v) or np.isnan(a):
                lo.append(0.0); hi.append(0.0)
            else:
                lo.append(v - a); hi.append(b - v)
        return np.array([lo, hi])

    w = 0.26
    fig, ax = plt.subplots(figsize=(8.6, 4.9))

    # 低于读入门槛的两档标灰，注明系统在这里不下结论。
    ax.axvspan(-0.5, 1.5, color="0.90", zorder=0)
    ax.text(0.5, 0.735, "密度 < 96 px　系统不下读数结论", ha="center", va="top",
            fontsize=9.5, color="0.30", zorder=5)
    ax.text(0.5, 0.665, "numpy 基线 30–60 px 的 P90 = 1.44（越界，未画）",
            ha="center", va="top", fontsize=8, color="0.45", zorder=5)

    series = [
        (x - w, geo_p90, yerr(geo, geo_p90), C_GEO, "几何法（现默认）"),
        (x, npy_plot, yerr(npy_seg, npy_plot), C_NPY, "numpy 逻辑回归"),
        (x + w, unet_p90, yerr(unet_seg, unet_p90), C_UNET, "U-Net（本次训练）"),
    ]
    for xpos, vals, ye, color, lab in series:
        ax.bar(xpos, vals, w, color=color, label=lab, yerr=ye, capsize=2.5,
               error_kw=dict(elinewidth=1.0, ecolor="0.40"), zorder=3)
        for xi, v in zip(xpos, vals):
            if np.isnan(v):
                continue
            dim = xi <= 1.0            # 灰档里的数字照写但调暗
            ax.text(xi, v + 0.02, "%.2f" % v, ha="center", va="bottom",
                    fontsize=8.5, color=("0.55" if dim else "0.15"), zorder=4)

    # 读入门槛线（96 px 落在 90–120 档内，画在两档交界处示意）
    ax.axvline(1.5, color="crimson", linestyle="--", linewidth=1, zorder=2)
    ax.text(1.55, 0.735, "读入门槛 %.0f px（120 × 0.8）" % READ_FLOOR_PX,
            color="crimson", fontsize=9, va="top", zorder=5)

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_xlabel("像素密度（px，判据线 120 px）", fontsize=11)
    ax.set_ylabel("读数误差 P90（%FS）", fontsize=11)
    ax.set_title("读数误差 P90 按像素密度分档：几何法在合成表盘上仍最优",
                 fontsize=12)
    ax.set_ylim(0, 0.75)   # 顶部 0.62–0.75 留给注记带，不与柱顶数值抢位置
    ax.legend(fontsize=9, loc="upper right", framealpha=0.92)
    ax.grid(axis="y", alpha=0.3, zorder=1)
    fig.tight_layout()
    fig.savefig(FIG / "reading_error.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("reading_error.png")


if __name__ == "__main__":
    mask_check()
    iou_compare()
    reading_error()
    print("三张图写到", FIG)
