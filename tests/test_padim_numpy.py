"""PaDiM 无 torch 版（板上用）与 torch 版逐图对分。

两边统计量同源（padim_cov.pt → padim_cov_stats.npz），特征网络同源（ResNet18 → padim_net{2,3}.onnx），
差别只剩缩放插值：torch 版用 torchvision 的 antialias 双线性，这里用 cv2。所以分数允许有小差，
但离阈值远的样本判定必须一致。缺权重或 torch 就 skip。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from patrol.common.imio import imread

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "training" / "runs" / "anomaly"


@pytest.fixture(scope="module")
def pair():
    pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    pytest.importorskip("onnxruntime")
    need = [RUNS / "padim_cov.pt", RUNS / "padim_cov_stats.npz", RUNS / "padim_net2.onnx", RUNS / "padim_net3.onnx"]
    if not all(p.exists() for p in need):
        pytest.skip("缺 PaDiM 权重：先跑 python -m training.export_padim_stats")
    patches = sorted((ROOT / "training" / "datasets" / "normal_patches" / "normal").glob("*.jpg"))[::40][:12]
    if len(patches) < 6:
        pytest.skip("缺正常裁片 training/datasets/normal_patches/normal")
    from patrol.perception.anomaly import PadimAnomaly, PadimNumpyAnomaly
    ref = PadimAnomaly(str(RUNS / "padim_cov.pt"))
    ours = PadimNumpyAnomaly(str(RUNS / "padim_cov_stats.npz"), str(RUNS / "padim_net2.onnx"),
                             str(RUNS / "padim_net3.onnx"), backend="onnx")
    return ref, ours, patches


def _samples(patches):
    import cv2
    out = []
    for p in patches:
        img = imread(p)
        out.append(img)
        bad = img.copy()                               # 盖一块暗斑当异常
        h, w = bad.shape[:2]
        cv2.rectangle(bad, (w // 4, h // 3), (w // 2, h * 2 // 3), (20, 20, 20), -1)
        out.append(bad)
        out.append(cv2.resize(img, (w * 3, h * 3)))    # 大于 256 的 ROI 走缩小插值
    return out


def test_scores_match_torch_version(pair):
    ref, ours, patches = pair
    diffs, flips = [], []
    for img in _samples(patches):
        a, b = ref.score(img), ours.score(img)
        diffs.append(abs(a.anomaly_score - b.anomaly_score))
        if abs(a.anomaly_score - ref.threshold) > 0.05 and a.is_anomaly != b.is_anomaly:
            flips.append((a.anomaly_score, b.anomaly_score))
    diffs = np.asarray(diffs)
    assert not flips, "离阈值远的样本判定不一致：%s" % flips
    assert float(np.median(diffs)) <= 0.01, "分数中位差 %.4f" % float(np.median(diffs))
    assert float(diffs.max()) <= 0.05, "分数最大差 %.4f" % float(diffs.max())
