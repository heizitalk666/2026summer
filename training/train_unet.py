#!/usr/bin/env python3
"""训 U-Net 去打 numpy 逻辑回归基线。

    python -m training.train_unet --data training/datasets/seg_combined --epochs 40

**这是在 train_segmenter.py 的 numpy 基线跑通之后的上一步。**基线把
"造数据 → 训练 → 导出 → 推理 → 接进读数链"整条链路先走通了，剩下的
就是换一个更强的模型，看针的 IoU 能提到多少。分工书里把这一步划给
L2（乙），显卡花在这里。

与 numpy 基线的对比口径**必须一致**，否则比出来的数字没有意义。两个模型
都在同一批 val ROI 上、用同一套规则算针的 IoU：

    pred_needle = P(needle) > 0.5
    IoU = (pred ∩ gt) / (pred ∪ gt)，gt = mask == 2

numpy 基线在其原生工作分辨率 128 上、U-Net 在 256 上，各自算各自的——
分辨率差异是每个模型的设计属性，不是要抹平的东西。

输出约定与 onnx_seg.py 一致，导出后 `configs` 里 `perception.segmenter:
{backend: onnx, weights: ...}` 就能接进读数链。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

from patrol.perception.segment.pixel import N_FEAT, WORK, features
from patrol.scene.gauges import SEG_NAMES
from training.train_segmenter import PER_IMAGE, QUOTA, crops_of

N_CLASS = 4
IGNORE = 255


# ------------------------------------------------------------------ 网络
class DoubleConv(nn.Module):
    def __init__(self, cin: int, cout: int):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(cin, cout, 3, padding=1, bias=False),
            nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
            nn.Conv2d(cout, cout, 3, padding=1, bias=False),
            nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
        )

    def forward(self, x):
        return self.conv(x)


class UNet(nn.Module):
    """从头搭的轻量 U-Net。不加载任何预训练权重——这台机器上 torchvision
    预训练下不到（见 docs/多模型协同.md 第五节），而且表盘结构简单，
    base=32 的 U-Net 已经足够。输出 (N, 4, H, W) logits，类别顺序与
    SEG_NAMES 一致：背景 / 面 / 针 / 刻度。
    """

    def __init__(self, cin: int = 3, cout: int = N_CLASS, base: int = 32):
        super().__init__()
        self.c1 = DoubleConv(cin, base)
        self.c2 = DoubleConv(base, base * 2)
        self.c3 = DoubleConv(base * 2, base * 4)
        self.c4 = DoubleConv(base * 4, base * 8)
        self.b = DoubleConv(base * 8, base * 16)
        self.pool = nn.MaxPool2d(2)
        self.u4 = nn.ConvTranspose2d(base * 16, base * 8, 2, 2)
        self.d4 = DoubleConv(base * 16, base * 8)
        self.u3 = nn.ConvTranspose2d(base * 8, base * 4, 2, 2)
        self.d3 = DoubleConv(base * 8, base * 4)
        self.u2 = nn.ConvTranspose2d(base * 4, base * 2, 2, 2)
        self.d2 = DoubleConv(base * 4, base * 2)
        self.u1 = nn.ConvTranspose2d(base * 2, base, 2, 2)
        self.d1 = DoubleConv(base * 2, base)
        self.out = nn.Conv2d(base, cout, 1)

    def forward(self, x):
        x1 = self.c1(x)
        x2 = self.c2(self.pool(x1))
        x3 = self.c3(self.pool(x2))
        x4 = self.c4(self.pool(x3))
        b = self.b(self.pool(x4))
        d = self.d4(torch.cat([self.u4(b), x4], 1))
        d = self.d3(torch.cat([self.u3(d), x3], 1))
        d = self.d2(torch.cat([self.u2(d), x2], 1))
        d = self.d1(torch.cat([self.u1(d), x1], 1))
        return self.out(d)


# ------------------------------------------------------------------ 数据
def collect_rois(root: Path, split: str) -> list[tuple[np.ndarray, np.ndarray]]:
    """按检测框把整帧切成表盘 ROI，只留含指针的。返回 (img, mask) 原始分辨率。

    与 train_segmenter.load_split 同一条 crops_of 逻辑，保证两个模型喂的是
    同一批 ROI。
    """
    rois = []
    imgs = sorted((root / "images" / split).glob("*.jpg"))
    for im in imgs:
        m = cv2.imread(str(root / "masks" / split / (im.stem + ".png")), 0)
        img = cv2.imread(str(im))
        if m is None or img is None or img.shape[:2] != m.shape[:2]:
            continue
        for sub_img, sub_m in crops_of(root, split, im.stem, img, m):
            if not (sub_m == 2).any():          # 只要含指针的 ROI
                continue
            if min(sub_img.shape[:2]) < 24:
                continue
            rois.append((sub_img, sub_m))
    return rois


def resize_pair(img: np.ndarray, mask: np.ndarray, size: int):
    inter = cv2.INTER_AREA if max(img.shape[:2]) > size else cv2.INTER_CUBIC
    im = cv2.resize(img, (size, size), interpolation=inter)
    ma = cv2.resize(mask, (size, size), interpolation=cv2.INTER_NEAREST)
    return im, ma


class SegDataset(Dataset):
    def __init__(self, rois: list, size: int):
        self.size = size
        self.imgs = []
        self.masks = []
        for img, mask in rois:
            im, ma = resize_pair(img, mask, size)
            self.imgs.append(im)
            self.masks.append(ma)

    def __len__(self):
        return len(self.imgs)

    def __getitem__(self, i):
        x = torch.from_numpy(self.imgs[i].astype(np.float32) / 255.0).permute(2, 0, 1)
        y = torch.from_numpy(self.masks[i].astype(np.int64))
        return x, y


def class_weights(rois: list) -> torch.Tensor:
    """按像素频率的倒数定权重，再压到 [0.5, 5] 区间。

    背景和盘面占了九成像素，不压它们模型会学成"全判背景"；压过头又会让
    针那一类疯狂误报。压进一个小区间是取稳。
    """
    cnt = np.zeros(N_CLASS, np.float64)
    for _img, mask in rois:
        for c in range(N_CLASS):
            cnt[c] += int((mask == c).sum())
    cnt = np.maximum(cnt, 1.0)
    w = cnt.sum() / (N_CLASS * cnt)
    w = np.clip(w, 0.5, 5.0)
    return torch.tensor(w, dtype=torch.float32)


# ------------------------------------------------------------------ 评测
#
# 评测口径与 train_segmenter.py 的 evaluate() **完全一致**：按 QUOTA 在
# WORK=128 网格上重新平衡采样，再算逐类 IoU。这样 U-Net 的针 IoU 才能和
# 基线 0.383（同口径）直接比较——换成"整幅 argmax IoU"会得到另一个更严苛、
# 但和基线不可比的数字，那对这份交付没有意义。
def sample_pixels(rois: list, rng: np.random.Generator):
    """按 QUOTA 在 128 网格上采样。返回 [(img, pick_idx, pick_label), ...]。"""
    out = []
    for img, mask in rois:
        lab = cv2.resize(mask, (WORK, WORK),
                         interpolation=cv2.INTER_NEAREST).reshape(-1)
        picks, labels = [], []
        for c in range(N_CLASS):
            idx = np.where(lab == c)[0]
            if idx.size == 0:
                continue
            k = min(idx.size, max(1, int(PER_IMAGE * QUOTA.get(c, 0.25))))
            p = rng.choice(idx, size=k, replace=False)
            picks.append(p)
            labels.append(lab[p])
        if picks:
            out.append((img, np.concatenate(picks), np.concatenate(labels)))
    return out


def _iou_from_preds(samples, pred_fn) -> dict:
    inter = np.zeros(N_CLASS, np.float64)
    union = np.zeros(N_CLASS, np.float64)
    for img, picks, labels in samples:
        pred = pred_fn(img)[picks]
        for c in range(N_CLASS):
            inter[c] += int(((pred == c) & (labels == c)).sum())
            union[c] += int(((pred == c) | (labels == c)).sum())
    return {SEG_NAMES[c]: (inter[c] / union[c] if union[c] else float("nan"))
            for c in range(N_CLASS)}


def _unet_pred_fn(model, size, device):
    def pred_fn(img):
        im, _ = resize_pair(img, img, size)
        x = torch.from_numpy(im.astype(np.float32) / 255.0).permute(2, 0, 1)[None].to(device)
        with torch.no_grad():
            logits = model(x)[0]                          # (N_CLASS, size, size)
        p = F.softmax(logits, dim=0).cpu().numpy().transpose(1, 2, 0)
        p = cv2.resize(p, (WORK, WORK), interpolation=cv2.INTER_LINEAR)
        return p.argmax(axis=2).reshape(-1)               # 与 128 网格对齐
    return pred_fn


def _lr_pred_fn(weights: str):
    d = np.load(weights)
    W = d["W"].astype(np.float32)
    mu = d["mu"].astype(np.float32)
    sigma = d["sigma"].astype(np.float32)

    def pred_fn(img):
        f = features(img).reshape(-1, N_FEAT)             # (WORK*WORK, N_FEAT)
        return np.argmax(((f - mu) / sigma) @ W, axis=1)
    return pred_fn


# ------------------------------------------------------------------ 主流程
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="训练 U-Net 打 numpy 基线")
    ap.add_argument("--data", default="training/datasets/seg_combined")
    ap.add_argument("--out", default="training/runs/seg/unet.pt")
    ap.add_argument("--onnx", default="training/runs/seg/unet.onnx")
    ap.add_argument("--baseline", default="training/runs/seg/pixel_combined.npz")
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)

    torch.manual_seed(a.seed)
    np.random.seed(a.seed)
    device = a.device if torch.cuda.is_available() and a.device == "cuda" else "cpu"
    print("device: %s" % device)

    root = Path(a.data)
    if not (root / "images" / "train").exists():
        raise SystemExit("数据集不存在：%s（先跑 gen_synthetic 和 --from-paddlex 再合起来）"
                         % root)

    t0 = time.time()
    train_rois = collect_rois(root, "train")
    val_rois = collect_rois(root, "val")
    print("ROI：train %d / val %d（采集耗时 %.1f s）" %
          (len(train_rois), len(val_rois), time.time() - t0))

    w = class_weights(train_rois)
    print("类别权重", {SEG_NAMES[c]: round(float(w[c]), 2) for c in range(N_CLASS)})

    train_ds = SegDataset(train_rois, a.size)
    train_dl = DataLoader(train_ds, batch_size=a.batch, shuffle=True,
                          num_workers=0, drop_last=True)
    model = UNet().to(device)
    opt = torch.optim.Adam(model.parameters(), lr=a.lr)
    crit = nn.CrossEntropyLoss(weight=w.to(device), ignore_index=IGNORE)

    # 采样一次、固定下来，两个模型都在同一批像素上算 IoU，口径一致
    val_rng = np.random.default_rng(a.seed)
    val_samples = sample_pixels(val_rois, val_rng)
    unet_pred = _unet_pred_fn(model, a.size, device)

    best = {"iou": -1.0, "needle": float("nan")}
    for ep in range(1, a.epochs + 1):
        model.train()
        total = 0.0
        for x, y in train_dl:
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            loss = crit(model(x), y)
            loss.backward()
            opt.step()
            total += float(loss.item()) * x.size(0)
        report = _iou_from_preds(val_samples, unet_pred)
        if report["needle"] > best["iou"]:
            best["iou"] = report["needle"]
            best["needle"] = report["needle"]
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            torch.save(model.state_dict(), a.out)
        if ep % 5 == 0 or ep == a.epochs:
            print("epoch %3d  loss %.4f  val IoU bg %.3f face %.3f "
                  "needle %.3f ticks %.3f" %
                  (ep, total / max(1, len(train_ds)),
                   report["background"], report["face"],
                   report["needle"], report["ticks"]))

    # 加载最佳权重做最终评测，并对比 numpy 基线
    model.load_state_dict(torch.load(a.out, map_location=device))
    unet_iou = _iou_from_preds(val_samples, unet_pred)
    print("\n=== 最终（最佳权重）===")
    print("U-Net    ", {k: round(v, 3) for k, v in unet_iou.items()})
    print("针的 IoU = %.3f" % unet_iou["needle"])

    base_iou = None
    if Path(a.baseline).exists():
        base_iou = _iou_from_preds(val_samples, _lr_pred_fn(a.baseline))
        print("numpy基线", {k: round(v, 3) for k, v in base_iou.items()})
        print("numpy 针 IoU = %.3f" % base_iou["needle"])

    # 导 ONNX（约定与 onnx_seg.py 一致）
    model.eval().cpu()
    torch.onnx.export(
        model, torch.zeros(1, 3, a.size, a.size), a.onnx,
        opset_version=12, input_names=["images"], output_names=["logits"],
        dynamic_axes={"images": {2: "h", 3: "w"}, "logits": {2: "h", 3: "w"}})
    print("\n权重 %s  ONNX %s" % (a.out, a.onnx))

    meta = {
        "unet_needle_iou": unet_iou["needle"],
        "unet_iou": unet_iou,
        "baseline_needle_iou": base_iou["needle"] if base_iou else None,
        "baseline_iou": base_iou,
        "n_train_roi": len(train_rois),
        "n_val_roi": len(val_rois),
        "size": a.size,
    }
    out_json = Path(a.out).with_suffix(".json")
    out_json.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print("结果写到 %s" % out_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
