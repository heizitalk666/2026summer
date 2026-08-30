#!/usr/bin/env python3
"""训练像素级分割：从合成掩膜里学"哪些像素是指针"。

    python -m training.gen_synthetic --n 300 --out training/datasets/synth
    python -m training.train_segmenter --data training/datasets/synth
    # → training/runs/seg/pixel.npz，配置里把 perception.segmenter.backend 改成 npz

**这个脚本训的是一个逻辑回归，不是 U-Net——这是刻意的。**

它的作用是把"造数据 → 训练 → 导出 → 推理 → 接进读数链"这条链路完整跑通，
并给出一个诚实的基线：换成 U-Net 之后，起码知道该跟谁比。链路走不通的话，
再大的模型也只是纸面上的。

真要训 U-Net，改动是局部的：
  1. 这个脚本换成你们的训练循环（数据加载与评测口径直接抄下面的）
  2. 用 training/export_onnx.py 导成 ONNX
  3. 配置改成 `perception.segmenter: {backend: onnx, weights: ...}`
patrol/ 下一行不用动——分割是走 ISegmenter 接口接进去的。

**评测口径特意不用整体准确率。**背景占了九成以上像素，全判背景就有 90 %+
的准确率，而那正是最没用的模型。这里按类算 IoU，并且单独把"针"那一类拎
出来——读数精度只取决于它。
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from patrol.perception.segment.pixel import (N_CLASS, N_FEAT, WORK,
                                             features, softmax)
from patrol.scene.gauges import SEG_NAMES

#: 每块 ROI 最多采多少像素。ROI 归一化后只有 128×128 = 16384 个像素，
#: 采太多等于把同一块表反复喂进去。
PER_IMAGE = 2400
#: 类别采样配额。**背景压到一成**：不压的话它占九成以上，损失函数会被它
#: 主导，模型学成"全判背景"且准确率高达 92 %。
QUOTA = {0: 0.10, 1: 0.25, 2: 0.40, 3: 0.25}


def crops_of(root: Path, split: str, stem: str, img, mask, *, margin=0.14):
    """按检测框把整帧切成一块块表盘 ROI。

    **必须按目标切，不能整帧算。**推理时 `NpzSegmenter.segment()` 拿到的
    就是一块表盘 ROI，然后归一到 128×128；训练要是拿整帧去归一，同一块表
    在训练时只占几个像素、推理时占满画面，两边的尺度差了一个数量级——
    特征里那根固定长度的线状核就完全失去意义。第一版正是这么错的，针的
    验证 IoU 只有 0.02，看上去像"模型学不动"，其实是数据喂错了。
    """
    lf = root / "labels" / split / (stem + ".txt")
    if not lf.exists():
        return
    H, W = mask.shape[:2]
    for line in lf.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        _c, cx, cy, bw, bh = parts
        cx, cy, bw, bh = float(cx) * W, float(cy) * H, float(bw) * W, float(bh) * H
        mx, my = bw * margin, bh * margin
        x1 = max(0, int(round(cx - bw / 2 - mx)))
        y1 = max(0, int(round(cy - bh / 2 - my)))
        x2 = min(W, int(round(cx + bw / 2 + mx)))
        y2 = min(H, int(round(cy + bh / 2 + my)))
        if x2 - x1 < 24 or y2 - y1 < 24:
            continue                        # 太小的 ROI 推理时也会被跳过
        yield img[y1:y2, x1:x2], mask[y1:y2, x1:x2]


def load_split(root: Path, split: str, rng: np.random.Generator, limit=None):
    """从每块表盘 ROI 里采样像素特征。返回 (X, y)。"""
    imgs = sorted((root / "images" / split).glob("*.jpg"))
    if limit:
        imgs = imgs[:limit]
    X, Y = [], []
    n_roi = 0
    for im in imgs:
        m = cv2.imread(str(root / "masks" / split / (im.stem + ".png")), 0)
        img = cv2.imread(str(im))
        if m is None or img is None or img.shape[:2] != m.shape[:2]:
            continue
        for sub_img, sub_m in crops_of(root, split, im.stem, img, m):
            # 只留真有指针的 ROI：指示灯、开关的掩膜整块都是 face，
            # 拿它们训"针 vs 刻度"是纯噪声
            if not (sub_m == 2).any():
                continue
            n_roi += 1
            f = features(sub_img).reshape(-1, N_FEAT)
            # 标签跟着缩到工作分辨率，**必须最近邻**：插值出来的 1.5 不是类别
            lab = cv2.resize(sub_m, (WORK, WORK),
                             interpolation=cv2.INTER_NEAREST).reshape(-1)
            for c in range(N_CLASS):
                idx = np.where(lab == c)[0]
                if idx.size == 0:
                    continue
                k = min(idx.size, max(1, int(PER_IMAGE * QUOTA.get(c, 0.25))))
                pick = rng.choice(idx, size=k, replace=False)
                X.append(f[pick])
                Y.append(lab[pick])
    if not X:
        raise SystemExit("没有可用样本，先跑 training/gen_synthetic.py 生成数据集")
    print("  %s：%d 帧里切出 %d 块含指针的 ROI" % (split, len(imgs), n_roi))
    return np.concatenate(X), np.concatenate(Y).astype(np.int64)


def fit(X, y, *, epochs=120, lr=0.5, l2=1e-4, seed=0):
    """多类逻辑回归，全批梯度下降。样本量在十万级，全批比小批更稳。"""
    rng = np.random.default_rng(seed)
    mu, sigma = X.mean(0), X.std(0) + 1e-6
    Z = (X - mu) / sigma
    W = rng.normal(0, 0.01, (N_FEAT, N_CLASS)).astype(np.float32)
    onehot = np.zeros((len(y), N_CLASS), np.float32)
    onehot[np.arange(len(y)), y] = 1.0
    n = len(y)
    for ep in range(epochs):
        p = softmax(Z @ W)
        grad = Z.T @ (p - onehot) / n + l2 * W
        W -= lr * grad
        if (ep + 1) % 40 == 0:
            loss = float(-np.log(np.clip(p[np.arange(n), y], 1e-9, None)).mean())
            print("  epoch %3d   loss %.4f" % (ep + 1, loss))
    return W.astype(np.float32), mu.astype(np.float32), sigma.astype(np.float32)


def evaluate(W, mu, sigma, X, y) -> dict:
    """按类 IoU。**不报整体准确率**——背景占九成，那个数字只会骗人。"""
    pred = np.argmax(((X - mu) / sigma) @ W, axis=1)
    out = {}
    for c in range(N_CLASS):
        inter = int(((pred == c) & (y == c)).sum())
        union = int(((pred == c) | (y == c)).sum())
        out[SEG_NAMES[c]] = (inter / union) if union else float("nan")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="训练像素级分割（逻辑回归基线）")
    ap.add_argument("--data", default="training/datasets/synth")
    ap.add_argument("--out", default="training/runs/seg/pixel.npz")
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--lr", type=float, default=0.5)
    ap.add_argument("--limit", type=int, default=None, help="只用前 N 帧（调试用）")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)

    root = Path(a.data)
    if not (root / "images" / "train").exists():
        raise SystemExit("数据集不存在：%s\n先跑 python -m training.gen_synthetic" % root)
    rng = np.random.default_rng(a.seed)
    t0 = time.time()
    Xtr, ytr = load_split(root, "train", rng, a.limit)
    Xva, yva = load_split(root, "val", rng, a.limit)
    print("训练样本 %d，验证样本 %d，特征 %d 维（采样耗时 %.1f s）"
          % (len(ytr), len(yva), N_FEAT, time.time() - t0))
    print("类别分布（训练）", {SEG_NAMES[c]: int((ytr == c).sum())
                               for c in range(N_CLASS)})

    W, mu, sigma = fit(Xtr, ytr, epochs=a.epochs, lr=a.lr, seed=a.seed)
    tr = evaluate(W, mu, sigma, Xtr, ytr)
    va = evaluate(W, mu, sigma, Xva, yva)
    print("\n按类 IoU        %-10s %-10s" % ("训练", "验证"))
    for c in range(N_CLASS):
        n = SEG_NAMES[c]
        print("  %-12s %-10.3f %-10.3f" % (n, tr[n], va[n]))

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, W=W, mu=mu, sigma=sigma, val_iou=np.float32(va["needle"]),
             n_feat=np.int32(N_FEAT), n_class=np.int32(N_CLASS))
    print("\n权重写到 %s（%.1f KB）" % (out, out.stat().st_size / 1024))
    print("启用：configs 里设 perception.segmenter.backend: npz")

    if va["needle"] < 0.15:
        print("\n⚠ 针那一类的验证 IoU 只有 %.3f。读数精度只取决于这一类，"
              "这个数太低时**不要**启用它——几何法会更好。" % va["needle"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
