"""把 PaDiM 全协方差权重里的统计量导成 numpy，板上打分不再需要 torch。

    python -m training.export_padim_stats
    python -m training.export_padim_stats --weights training/runs/anomaly/padim_cov.pt \
        --out training/runs/anomaly/padim_cov_stats.npz

padim_cov.pt 里除了统计量没有网络参数：特征来自 ResNet18 的 layer2 / layer3，已经由
training/export_padim_onnx.py 导成 padim_net2.onnx / padim_net3.onnx（RKNN 版见
deliverables/丙-异常/rknn/）。打分所需的只剩这几样：

    mu   [P, k]      每个位置、所选 k 个通道的均值
    inv  [P, k, k]   协方差的逆
    idx  [P, k]      每个位置选中的通道下标（在 128 + 256 = 384 维拼接特征里）
    d_mu, d_sigma    训练集分数分布，用来做 σ 归一化
    topk_frac        取马氏距离最大的这一比例求均值

打分公式与 patrol/perception/anomaly.py 的 PadimAnomaly.score 逐项一致，
PadimNumpyAnomaly 负责板上那一半。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def export(weights: Path, out: Path) -> dict:
    import torch
    ck = torch.load(str(weights), map_location="cpu", weights_only=False)
    for k in ("mu", "inv", "idx"):
        if k not in ck:
            raise SystemExit("%s 不是全协方差版 PaDiM 权重（缺 %s）" % (weights, k))
    arrays = {
        "mu": ck["mu"].float().numpy(),
        "inv": ck["inv"].float().numpy(),
        "idx": ck["idx"].long().numpy().astype(np.int32),
        "d_mu": np.float64(ck.get("d_mu", 0.0)),
        "d_sigma": np.float64(max(1e-6, float(ck.get("d_sigma", 1.0)))),
        "topk_frac": np.float64(ck.get("topk_frac", 0.1)),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, **arrays)
    info = {k: (list(v.shape) if getattr(v, "ndim", 0) else float(v)) for k, v in arrays.items()}
    print("已导出 %s（%.1f MB）%s" % (out, out.stat().st_size / 1e6, info))
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PaDiM 统计量导出为 npz")
    ap.add_argument("--weights", default=str(ROOT / "training/runs/anomaly/padim_cov.pt"))
    ap.add_argument("--out", default=str(ROOT / "training/runs/anomaly/padim_cov_stats.npz"))
    a = ap.parse_args(argv)
    export(Path(a.weights), Path(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
