#!/usr/bin/env python3
"""生成乙（L2 分割）交付的三张图。跑一遍即可复现：

    python deliverables/乙-分割/make_figures.py

数字全部来自实测（训练日志 + bench_models 输出），不是手编的。跑这个脚本
需要 matplotlib 与 opencv；中文字体按 雅黑 → 黑体 → 思源 → 文泉驿 的顺序回退。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# 中文字体按优先级回退：Windows 上取雅黑/黑体，Linux 上取思源或文泉驿，
# 都没有才落到 DejaVu Sans（此时中文会显示为方块，图不可用）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                   "Noto Sans CJK SC", "Source Han Sans SC",
                                   "WenQuanYi Zen Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

HERE = Path(__file__).resolve().parent
FIG = HERE / "figures"
REPO = HERE.parent.parent


def mask_check():
    """两张核对图并排：合成掩膜（gen_synthetic --preview）与 PaddleX 真实标注
    叠加（--from-paddlex 的 check/）。掩膜错位在数字上看不出来，只能画出来看。
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
    # 注记原来落在 x=4.02，超出三根柱子的坐标范围，tight bbox 会把画布拉宽并压窄坐标区；
    # 贴着虚线放又会压在柱子上，改放到左上角的空白处，用文字说明虚线含义
    ax.text(0.02, 0.95, "红色虚线：旧基线 0.182（文档记录值）", color="crimson",
            fontsize=9.5, ha="left", va="top", transform=ax.transAxes)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG / "iou_compare.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("iou_compare.png")


def reading_error():
    """读数误差按像素密度分档：几何法 vs numpy 基线 vs U-Net（核心图）。

    数据来自 bench_models --only reading，误差取各档中位数（%FS）。
    """
    bands = ["30–60", "60–90", "90–120", "120–180", "180–320"]
    geo = [0.06, 0.12, 0.13, 0.10, 0.14]
    npy = [0.55, 0.18, 0.20, 0.13, 0.12]
    unet = [0.07, 0.16, 0.14, 0.19, 0.14]

    x = np.arange(len(bands))
    w = 0.26
    fig, ax = plt.subplots(figsize=(8.0, 4.6))
    ax.bar(x - w, geo, w, label="几何法（现默认）", color="#4a90d9")
    ax.bar(x, npy, w, label="numpy 逻辑回归", color="#9aa5b1")
    ax.bar(x + w, unet, w, label="U-Net（本次训练）", color="#2e7d32")

    ax.set_xticks(x)
    ax.set_xticklabels(bands)
    ax.set_xlabel("像素密度（px，判据线 120 px）", fontsize=11)
    ax.set_ylabel("读数误差 %FS（中位数）", fontsize=11)
    ax.set_title("读数误差按像素密度分档：学习法未在合成表盘上超过几何法", fontsize=12)
    ax.axvline(2.5, color="crimson", linestyle="--", linewidth=1)
    ax.text(2.52, 0.52, "判据线 120 px", color="crimson", fontsize=9)
    ax.legend(fontsize=10)
    ax.set_ylim(0, 0.6)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG / "reading_error.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("reading_error.png")


if __name__ == "__main__":
    mask_check()
    iou_compare()
    reading_error()
    print("三张图写到", FIG)
