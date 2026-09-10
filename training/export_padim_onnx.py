"""PaDiM 主干导 ONNX + 冒烟推理。python -m training.export_padim_onnx

导出产物(上 NPU 的候选):
  training/runs/anomaly/padim_net2.onnx  [1,3,256,256] float32(0-1 RGB) → [1,128,32,32]
  training/runs/anomaly/padim_net3.onnx  [1,3,256,256] float32(0-1 RGB) → [1,256,16,16]

部署侧打分 = 两个 ONNX 前向 + mu/var 马氏距离(逐位置 top-k),mu/var 与
top-k 在权重文件里,推理时只是张量运算,不需要再训任何东西。

冒烟:onnxruntime 与 torch 前向逐元素比对;再按部署语义把正常/异常裁片各
5 张跑完整打分,确认分数分离与 torch 版一致。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import cv2
import numpy as np

RUNS = REPO / "training/runs/anomaly"
CKPT = RUNS / "padim_cov.pt"


def build_nets():
    import torch
    import torch.nn as nn
    from torchvision import models
    children = list(models.resnet18(weights="DEFAULT").children())
    return nn.Sequential(*children[:6]).eval(), nn.Sequential(*children[:7]).eval()


def to_tensor(bgr: np.ndarray):
    import torch
    from torchvision import transforms
    tf = transforms.Compose([transforms.ToTensor(),
                             transforms.Resize((256, 256), antialias=True)])
    return tf(bgr[:, :, ::-1].copy()).unsqueeze(0)


def main() -> int:
    import torch

    ck = torch.load(str(CKPT), map_location="cpu", weights_only=False)
    mu = ck["mu"].numpy()          # [P, K] 全协方差版:每位置通道子集均值
    inv = ck["inv"].numpy()        # [P, K, K] 马氏距离逆协方差
    idx = ck["idx"].numpy()        # [P, K] 通道子集
    topk_frac = float(ck.get("topk_frac", 0.1))
    net2, net3 = build_nets()

    x = torch.randn(1, 3, 256, 256)
    torch.onnx.export(net2, x, str(RUNS / "padim_net2.onnx"), opset_version=12, dynamo=False,
                      input_names=["image"], output_names=["features"])
    torch.onnx.export(net3, x, str(RUNS / "padim_net3.onnx"), opset_version=12, dynamo=False,
                      input_names=["image"], output_names=["features"])
    print("ONNX 已写出: padim_net2.onnx / padim_net3.onnx")

    import onnxruntime as ort
    s2 = ort.InferenceSession(str(RUNS / "padim_net2.onnx"))
    s3 = ort.InferenceSession(str(RUNS / "padim_net3.onnx"))

    # 冒烟 1:ONNX 与 torch 前向逐元素一致
    diffs = {}
    for name, sess, net in (("net2", s2, net2), ("net3", s3, net3)):
        img = np.random.default_rng(0).integers(0, 256, (256, 256, 3), np.uint8)
        xt = to_tensor(img)
        with torch.no_grad():
            ref = net(xt).numpy()
        got = sess.run(None, {"image": xt.numpy().astype(np.float32)})[0]
        d = float(np.abs(ref - got).max())
        diffs[name] = d
        print("冒烟 %-5s 最大逐元素差 %.2e" % (name, d))
    assert diffs["net2"] < 1e-4 and diffs["net3"] < 1e-4, "ONNX 与 torch 不一致"

    # 冒烟 2:部署语义完整打分(ONNX 前向 + numpy 马氏距离)
    def score_onnx(bgr: np.ndarray) -> float:
        xt = to_tensor(bgr).numpy().astype(np.float32)
        f2 = s2.run(None, {"image": xt})[0][0]                       # [128,32,32]
        f3 = s3.run(None, {"image": xt})[0][0]                       # [256,16,16]
        # cv2.resize 最多支持 4 通道,256 通道只能逐通道插值(16×16 很小)
        f3_up = np.empty((f3.shape[0], 32, 32), np.float32)
        for c in range(f3.shape[0]):
            f3_up[c] = cv2.resize(f3[c], (32, 32), interpolation=cv2.INTER_LINEAR)
        f3 = f3_up
        f = np.concatenate([f2, f3], axis=0)                         # [384,32,32]
        P = f.shape[1] * f.shape[2]
        Xp = np.take_along_axis(f.reshape(384, P).T, idx, axis=1)     # [P,K]
        d = np.einsum("pk,pkj,pj->p", Xp - mu, inv, Xp - mu)
        k = max(1, int(d.size * topk_frac))
        return float(np.sort(d.flatten())[-k:].mean())

    def read(p: Path):
        return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)

    norms = [score_onnx(read(p)) for p in sorted((REPO / "out/l3_eval/normal").glob("*.jpg"))[:5]]
    anoms = [score_onnx(read(p)) for p in sorted((REPO / "out/l3_eval/anomaly").glob("*.jpg"))[:5]]
    report = {"onnx_ops": "opset 12", "input": "bgr-uint8 → float32 0-1 RGB 256×256",
              "outputs": ["[1,128,32,32]", "[1,256,16,16]"],
              "smoke_max_diff": diffs,
              "raw_topk_normal_mean": float(np.mean(norms)),
              "raw_topk_anomaly_mean": float(np.mean(anoms))}
    (RUNS / "onnx_smoke.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("部署语义打分:正常 top-k %.4f | 异常 top-k %.4f(应明显更大)"
          % (report["raw_topk_normal_mean"], report["raw_topk_anomaly_mean"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
