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
    """**构造失败必须抛 FileNotFoundError 或 ValueError。**

    perception/node.py 只捕这两类，捕到就 warn 一句、把 segmenter 置 None、
    读数退回几何法——那是设计好的降级路径。抛别的类型不是"报错"，是让整个
    PerceptionNode 构造崩掉，感知进程直接不起来。

    实测会漏出去的两条（都不是假想）：

    ``InvalidProtobuf``
        ``backend`` 和 ``weights`` 是两个字段，而 npz 与 onnx **共用后者**。
        configs/system.yaml 里 weights 显式写着 ``pixel.npz``，所以"只把
        backend 改成 onnx"这个最自然的操作会拿 onnxruntime 去加载 npz。
        ``path.exists()`` 拦不住——文件确实在。而 InvalidProtobuf 的 MRO 是
        ``[InvalidProtobuf, Exception, BaseException, object]``，既不是
        ValueError 也不是 FileNotFoundError。

    ``ModuleNotFoundError``
        onnxruntime 在 requirements.txt 里标的是「可选」。没装又照文档切了
        onnx，构造就崩。OCR 那条路用 DisabledOcr 做了降级，分割这条原先没有
        对等处理。
    """

    def __init__(self, cfg=None, weights: str | Path | None = None) -> None:
        try:
            import onnxruntime as ort                # 延迟导入：可选依赖
        except ImportError as e:
            raise ValueError(
                "onnxruntime 未安装，无法用 onnx 分割后端"
                "（requirements.txt 里它是可选依赖）：%s" % e) from e

        g = (lambda k, d: d) if cfg is None else (lambda k, d: cfg.get(k, d))
        path = Path(weights or g("perception.segmenter.weights",
                                 "training/runs/seg/unet.onnx"))
        if not path.exists():
            raise FileNotFoundError("分割 ONNX 权重不存在：%s" % path)
        self.size = int(g("perception.segmenter.input_size", 256))
        # U-Net 有 4 次 2× 下采样，跳连要求两边尺寸对得上。size 不是 16 的倍数
        # 时，推理会炸在 Concat（"Axis 2 has mismatched dimensions of 31 and 30"）
        # ——而 segment() 是裸 except，于是**每一帧都静默退回几何法，日志却还在说
        # 「分割级联已启用」**。实测 250 / 300 就是这个下场，128/224/256/320 正常。
        # 与 training/export_onnx.py 的 --seg-size 必须一致。
        if self.size % 16:
            raise ValueError(
                "perception.segmenter.input_size 必须是 16 的倍数"
                "（U-Net 跳连要求），当前是 %d" % self.size)
        self.min_side = int(g("perception.segmenter.min_side_px", 40))
        try:
            self.sess = ort.InferenceSession(
                str(path), providers=["CPUExecutionProvider"])
        except Exception as e:                                 # noqa: BLE001
            raise ValueError(
                "分割 ONNX 权重加载失败：%s —— weights 是不是还指着 npz？"
                "（backend 与 weights 是两个字段，切 onnx 时两个都要改）"
                "原始错误：%s" % (path, e)) from e
        self.iname = self.sess.get_inputs()[0].name
        self.path = str(path)
        #: 推理失败只在第一次记日志，之后计数，避免 10 Hz 刷屏。
        self._fail_n = 0

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
        except Exception as e:                                 # noqa: BLE001
            # **不要静默。**返回 None 会让读数退回几何法，那本身是对的；
            # 但一声不吭地退，就出现了 tests/test_segment.py 开篇点名的头号
            # 风险——「看起来在用、其实没在用」：node 日志说「分割级联已启用」，
            # 而每一帧的 seg_used 都是 False，没人查得出来。
            self._fail_n += 1
            if self._fail_n == 1:
                import logging
                logging.getLogger("patrol.segment").warning(
                    "分割 ONNX 推理失败，本帧退回几何法（后续同类失败只计数）："
                    "input_size=%d weights=%s —— %s", self.size, self.path, e)
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
        d = {"name": "onnx-seg", "backend": "onnxruntime", "offline": True,
             "weights": self.path, "input_size": self.size}
        if self._fail_n:
            d["infer_failures"] = self._fail_n
        return d
