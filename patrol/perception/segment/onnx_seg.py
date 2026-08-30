"""ONNX 分割推理。给你们训完 U-Net / YOLO-seg 之后接上用。

**这个文件里没有任何模型结构，只有约定。**约定写死三条，是为了让训练侧
可以随便换框架而部署侧不用改：

    输入   (1, 3, H, W)  float32  BGR，除以 255，不做均值方差归一化
    输出   (1, C, H, W)  float32  未过 softmax 的 logits，C = 4
    类别   与 scene/gauges.SEG_LABELS 同序：背景 / 面 / 针 / 刻度

选 ONNX 而不是直接 torch，是因为 RK3576 的 RKNN 工具链吃的就是 ONNX——
同一份权重，在电脑上用 onnxruntime 跑，在板子上转 RKNN 跑，中间不用改
任何代码。training/export_onnx.py 负责导出并当场跑一遍冒烟推理，
把"导出成功但形状不对"这类问题挡在训练侧。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from patrol.perception.segment.base import GaugeMask, ISegmenter
from patrol.perception.segment.pixel import N_CLASS, softmax


class OnnxSegmenter(ISegmenter):
    def __init__(self, cfg=None, weights: str | Path | None = None) -> None:
        import onnxruntime as ort                    # 延迟导入：可选依赖

        g = (lambda k, d: d) if cfg is None else (lambda k, d: cfg.get(k, d))
        path = Path(weights or g("perception.segmenter.weights",
                                 "training/runs/seg/unet.onnx"))
        if not path.exists():
            raise FileNotFoundError("分割 ONNX 权重不存在：%s" % path)
        self.size = int(g("perception.segmenter.input_size", 256))
        self.min_side = int(g("perception.segmenter.min_side_px", 40))
        self.sess = ort.InferenceSession(
            str(path), providers=["CPUExecutionProvider"])
        self.iname = self.sess.get_inputs()[0].name
        self.path = str(path)

    def segment(self, patch: np.ndarray) -> GaugeMask | None:
        if patch is None or patch.size == 0:
            return None
        h, w = patch.shape[:2]
        if min(h, w) < self.min_side:
            return None
        x = cv2.resize(patch, (self.size, self.size),
                       interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
        x = np.transpose(x, (2, 0, 1))[None]
        try:
            out = self.sess.run(None, {self.iname: x})[0]
        except Exception:                                      # noqa: BLE001
            return None
        p = softmax(np.transpose(out[0], (1, 2, 0)))
        if p.shape[-1] != N_CLASS:
            return None
        # 概率图按最近邻放回原尺寸会产生锯齿；这里要的是软边缘，用线性
        p = cv2.resize(p, (w, h), interpolation=cv2.INTER_LINEAR)
        return GaugeMask(needle=p[..., 2].astype(np.float32),
                         face=p[..., 1].astype(np.float32),
                         ticks=p[..., 3].astype(np.float32), model="onnx-seg")

    def model_info(self) -> dict:
        return {"name": "onnx-seg", "backend": "onnxruntime", "offline": True,
                "weights": self.path, "input_size": self.size}
