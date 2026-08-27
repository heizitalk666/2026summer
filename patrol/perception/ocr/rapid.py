"""RapidOCR（ONNXRuntime 版）。

选它的理由是**能在这台机器上真跑起来**，而不是纸面指标：

- 15 MB 的包里**自带 ONNX 权重**（检测 DBNet + 方向分类 + 识别 CRNN），
  安装完不需要再从任何地方下载模型——巡检车上没有外网，云端也不该是
  感知的前置依赖
- 只依赖 onnxruntime，不需要 527 MB 的 torch。组里同学各自的笔记本上
  `pip install` 一行就能复现
- 同一份 ONNX 权重将来能直接进 RK3576 的 RKNN 工具链，部署侧不用换模型

实测：初始化 0.22 s，1920×1080 全图 0.33 s。**所以它只在复核期跑**——
巡航期 30 Hz 的预算里塞不下，也没必要：50 px 的表盘上根本没有可读的字。
"""
from __future__ import annotations

import threading

import numpy as np

from patrol.perception.ocr.base import IOcr, OcrLine, crop


class RapidOcr(IOcr):
    """线程安全的 RapidOCR 包装。

    引擎实例不保证可重入，而感知节点将来可能在多线程里调用它，所以加锁。
    锁的代价（复核期每次一两百毫秒串行）远小于并发踩坏推理会话的代价。
    """

    def __init__(self, cfg=None) -> None:
        from rapidocr_onnxruntime import RapidOCR      # 延迟导入：可选依赖

        g = (lambda k, d: d) if cfg is None else (lambda k, d: cfg.get(k, d))
        self.min_conf = float(g("perception.ocr.min_conf", 0.50))
        #: ROI 短边小于这个像素数就不跑——放大到位之前跑 OCR 纯属浪费 0.3 s。
        #: 120 px 正是像素密度判据线，两处用同一个数不是巧合：字能不能读出来
        #: 和针能不能量准，受制于同一个东西。
        self.min_side_px = float(g("perception.ocr.min_side_px", 120.0))
        #: 上采样到这个短边再送引擎。表盘上的刻度数字本来就小，直接送
        #: 原尺寸时识别率明显偏低。
        self.upscale_to = float(g("perception.ocr.upscale_to_px", 320.0))
        self._engine = RapidOCR()
        self._lock = threading.Lock()
        self._calls = 0

    # ------------------------------------------------------------------
    def read(self, image: np.ndarray, bbox=None, *, margin: float = 0.12
             ) -> list[OcrLine]:
        got = crop(image, bbox, margin)
        if got is None:
            return []
        patch, x0, y0 = got
        h, w = patch.shape[:2]
        if min(h, w) < self.min_side_px:
            return []
        scale = 1.0
        if min(h, w) < self.upscale_to:
            import cv2
            scale = float(self.upscale_to) / float(min(h, w))
            patch = cv2.resize(patch, (max(8, int(round(w * scale))),
                                       max(8, int(round(h * scale)))),
                               interpolation=cv2.INTER_CUBIC)
        try:
            with self._lock:
                self._calls += 1
                res, _elapsed = self._engine(patch)
        except Exception:                                      # noqa: BLE001
            # 引擎内部出错不该把复核带走：没有互证结果 = 结论更保守
            return []
        out: list[OcrLine] = []
        for item in (res or []):
            try:
                box, text, conf = item[0], str(item[1]), float(item[2])
            except (IndexError, TypeError, ValueError):
                continue
            if conf < self.min_conf or not text.strip():
                continue
            pts = np.asarray(box, dtype=np.float64).reshape(-1, 2) / scale
            out.append(OcrLine(
                text=text.strip(), conf=float(np.clip(conf, 0.0, 1.0)),
                bbox=(float(x0 + pts[:, 0].min()), float(y0 + pts[:, 1].min()),
                      float(x0 + pts[:, 0].max()), float(y0 + pts[:, 1].max()))))
        return out

    def model_info(self) -> dict:
        return {"name": "rapidocr-onnxruntime", "backend": "onnxruntime",
                "offline": True, "calls": self._calls,
                "min_conf": self.min_conf, "min_side_px": self.min_side_px}
