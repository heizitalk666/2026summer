#!/usr/bin/env python3
"""只评测，不训练：拿现成的 unet.pt 在 val 上复算四类 IoU。

    python -m training.eval_unet
    python -m training.eval_unet --data training/datasets/seg_combined

**为什么要单独有这个入口。** ``train_unet.py`` 的 IoU 是训练流程的副产品，
``main()`` 一上来就 ``collect_rois(root, "train")``（第 274 行），没有训练集
就跑不起来。而复核别人交付的权重时，需要的恰恰是"只有权重和 val、不重训"
——重训出来的是另一个模型，对不上账。

**口径与 train_unet.main() 逐行一致**：同一个 ``collect_rois`` / ``sample_pixels``
（QUOTA 在 128 网格上按类配额采样）/ ``_iou_from_preds``，默认 ``--seed 0``
与它的默认值相同，所以采到的像素是同一批。

--------------------------------------------------------------------
**这个脚本默认同时报 eval 与 train 两种模式，不是为了好看。**

``train_unet.py`` 的 ``_unet_pred_fn`` 原先**从不调用 ``model.eval()``**
（只有 ``torch.no_grad()``，那只关梯度、不切 BN）。于是 val 前向跑在 train
模式：BN 用的是当前这一张图的 batch 统计，而且每前向一次就改写一次
running stats。交付记录 ``deliverables/乙-分割/artifacts/unet.json`` 里的
``needle IoU 0.7784`` 就是在这个缺陷下算出来的。

现在 ``_unet_pred_fn`` 已修（加了 ``model.eval()``），所以 eval 那一列才是
**部署时真正会得到的精度**——``OnnxSegmenter`` 和 ONNX 导出走的都是 eval 语义。
train 那一列只用来对账：它应当接近 0.7784，从而证明"差异来自 BN 模式"这个
判断，而不是把交付方的数说成造假。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np
import torch

from patrol.scene.gauges import SEG_NAMES
from training.train_unet import (N_CLASS, UNet, _iou_from_preds, _lr_pred_fn,
                                 _unet_pred_fn, collect_rois, sample_pixels)


def _pred_fn_train_mode(model, size, device):
    """复刻**修复前**的行为：只 no_grad、不 eval，BN 留在 train 模式。

    仅用于对账，不要拿它的数当部署精度。
    """
    import cv2
    import torch.nn.functional as F

    from training.train_unet import WORK, resize_pair

    model.train()                      # ← 这正是修复前的实际状态

    def pred_fn(img):
        im, _ = resize_pair(img, img, size)
        x = torch.from_numpy(im.astype(np.float32) / 255.0)
        x = x.permute(2, 0, 1)[None].to(device)
        with torch.no_grad():
            logits = model(x)[0]
        p = F.softmax(logits, dim=0).cpu().numpy().transpose(1, 2, 0)
        p = cv2.resize(p, (WORK, WORK), interpolation=cv2.INTER_LINEAR)
        return p.argmax(axis=2).reshape(-1)
    return pred_fn


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="只评测 U-Net，不训练")
    ap.add_argument("--data", default="training/datasets/seg_combined")
    ap.add_argument("--weights", default="training/runs/seg/unet.pt")
    ap.add_argument("--baseline", default="training/runs/seg/pixel_combined.npz")
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--json", default=None, help="把结果落盘到这个路径")
    a = ap.parse_args(argv)

    root = REPO / a.data if not Path(a.data).is_absolute() else Path(a.data)
    if not (root / "images" / "val").is_dir():
        print("val 划分不存在：%s/images/val" % root, file=sys.stderr)
        return 1

    val_rois = collect_rois(root, "val")
    print("val 含针 ROI：%d 个" % len(val_rois))
    if not val_rois:
        print("一个含针 ROI 都没采到，检查掩膜里有没有类别 2", file=sys.stderr)
        return 1

    # 与 train_unet.main() 同一个种子、同一个采样函数 → 同一批像素
    val_samples = sample_pixels(val_rois, np.random.default_rng(a.seed))

    device = torch.device(a.device)
    model = UNet().to(device)
    sd = torch.load(REPO / a.weights, map_location=device)
    if isinstance(sd, dict) and "model" in sd and not isinstance(
            next(iter(sd.values())), torch.Tensor):
        sd = sd["model"]
    model.load_state_dict(sd)

    out = {"n_val_roi": len(val_rois), "size": a.size, "seed": a.seed,
           "weights": str(a.weights)}

    iou_eval = _iou_from_preds(val_samples, _unet_pred_fn(model, a.size, device))
    out["unet_iou_eval"] = {k: float(v) for k, v in iou_eval.items()}

    # 重新装一次权重：train 模式那一遍会改写 running stats，不能污染
    model.load_state_dict(sd)
    iou_train = _iou_from_preds(val_samples,
                                _pred_fn_train_mode(model, a.size, device))
    out["unet_iou_train_mode"] = {k: float(v) for k, v in iou_train.items()}

    base = REPO / a.baseline
    if base.exists():
        try:
            out["baseline_iou"] = {
                k: float(v) for k, v in
                _iou_from_preds(val_samples, _lr_pred_fn(str(base))).items()}
        except Exception as e:                             # noqa: BLE001
            out["baseline_iou_error"] = repr(e)

    print()
    print("%-12s %12s %12s %12s" % ("类别", "U-Net(eval)", "U-Net(train)", "npz 基线"))
    print("-" * 52)
    for c in range(N_CLASS):
        n = SEG_NAMES[c]
        b = out.get("baseline_iou", {}).get(n)
        print("%-12s %12.4f %12.4f %12s"
              % (n, iou_eval[n], iou_train[n],
                 "%.4f" % b if b is not None else "—"))
    print()
    print("交付记录（artifacts/unet.json）：needle 0.7784 / baseline 0.3837")
    print("**eval 那一列才是部署精度**；train 那一列用于对账修复前的口径。")

    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print("已落盘 %s" % a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
