"""YOLO 检测后端。

权重就位后把 configs/system.yaml 的 perception.detector 改成 yolo 即可，
上层一行不用改——这正是 IDetector 抽象存在的理由。

真机上还会多一层 RKNN：RK3576 的 NPU 不吃 .pt，要先量化导出。接口不变，
到时加一个 detector/rknn.py 就行。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from patrol.perception.detector.base import Detection, IDetector
from patrol.perception.detector.synthetic import CLASS_SIZE_M


class YoloDetector(IDetector):
    def __init__(self, cfg):
        self.cfg = cfg
        y = cfg.get("perception.yolo")
        self.device = str(y.get("device", "cpu"))
        self._paths = {"CRUISE": Path(y.get("weights_cruise")),
                       "VERIFY": Path(y.get("weights_verify"))}
        self._models: dict[str, object] = {}
        self._nms = float(cfg.get("perception.model.nms_iou", 0.45))
        self._in_w = int(cfg.get("perception.model.input_w", 640))
        self._in_h = int(cfg.get("perception.model.input_h", 640))
        self._cfg_models = {"CRUISE": dict(cfg.get("perception.model.cruise")),
                            "VERIFY": dict(cfg.get("perception.model.verify"))}
        # 类别名 → 本项目枚举。训练时用的就是这三个名字，见 training/
        self._classes = list(cfg.get("mission.first_release_classes",
                                     list(CLASS_SIZE_M)))

    def _model(self, stage: str):
        if stage in self._models:
            return self._models[stage]
        p = self._paths.get(stage) or self._paths["CRUISE"]
        if not p.exists():
            raise FileNotFoundError(
                "找不到权重 %s。先跑 training/train_detector.py，"
                "或把 perception.detector 改回 synthetic 用合成检测器跑通全链路。" % p)
        from ultralytics import YOLO
        m = YOLO(str(p))
        self._models[stage] = m
        return m

    def model_info(self, stage: str = "CRUISE") -> dict:
        c = self._cfg_models.get(stage, self._cfg_models["CRUISE"])
        return {"name": str(c.get("name", "yolo11s")),
                "input_w": self._in_w, "input_h": self._in_h,
                "quant": str(c.get("quant", "FP16")),
                "conf_threshold": float(c.get("conf_threshold", 0.25)),
                "nms_iou": self._nms}

    def infer(self, image: np.ndarray, *, conf_threshold: float,
              stage: str = "CRUISE") -> list[Detection]:
        m = self._model(stage)
        res = m.predict(image, conf=conf_threshold, iou=self._nms,
                        imgsz=self._in_w, device=self.device, verbose=False)
        out: list[Detection] = []
        for r in res:
            names = r.names
            for b in r.boxes:
                cls = names.get(int(b.cls[0]), "")
                if cls not in self._classes:
                    continue
                x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
                out.append(Detection(
                    defect_class=cls, confidence=float(b.conf[0]),
                    bbox=(x1, y1, x2, y2), source_target_id=None,
                    extra={"target_size_m": CLASS_SIZE_M.get(cls, 0.15),
                           # 真机上距离由 bbox 高度与先验尺寸反算，
                           # 精度有限，只用于像素密度估计（ICD §3.1）
                           "distance_m": _estimate_distance(
                               y2 - y1, CLASS_SIZE_M.get(cls, 0.15),
                               image.shape[0],
                               float(self.cfg.get("optics.hfov_at_1x_deg", 60.0)),
                               image.shape[1])}))
        return out

    def close(self) -> None:
        self._models.clear()


def _estimate_distance(bbox_h_px: float, size_m: float, img_h: int,
                       hfov_1x_deg: float, img_w: int, zoom: float = 1.0) -> float:
    """由 bbox 高度与先验物理尺寸反算距离。

    ICD §3.1 明说：精度有限，**只用于像素密度估计**，不能当测距结果用。
    真机上更靠谱的距离来自巡检位标定表（目标挂在哪个柜子上是标定过的）。
    """
    import math
    if bbox_h_px <= 1:
        return 8.0
    vfov = hfov_1x_deg * img_h / max(1, img_w)
    f_px = img_h / (2.0 * math.tan(math.radians(vfov) / 2.0))
    return float(max(0.3, min(30.0, f_px * size_m * zoom / bbox_h_px)))
