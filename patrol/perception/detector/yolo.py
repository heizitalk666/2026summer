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
                "找不到权重 %s（stage=%s）。\n"
                "两级权重不在版本库里（.gitignore 忽略 *.pt 与 training/runs/），"
                "全新 clone 必然缺它们。三条路选一条：\n"
                "  1. 把 perception.detector 改回 synthetic —— 全链路照样跑通，"
                "这是仓库默认；\n"
                "  2. 已有权重就放到上面这个路径，见 "
                "deliverables/甲-检测/artifacts/where.txt；\n"
                "  3. 自己训：python -m training.train_detector --stage %s。\n"
                "**不要改成缺权重自动退回 synthetic** —— 配置写着 yolo 却跑合成"
                "检测器，报出去的指标就是假的。" % (p, stage, stage.lower()))
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
                # 不带 distance_m。真机上距离要由 bbox 高度与先验尺寸反算，
                # 而反算必须代入**当前变焦倍率**——检测器拿不到云台状态
                # （IDetector 刻意不依赖场景/云台，这正是换后端时上层一行
                # 不改的前提）。所以距离由 node.py 反算，见
                # scene/optics.py:distance_from_bbox_height。
                out.append(Detection(
                    defect_class=cls, confidence=float(b.conf[0]),
                    bbox=(x1, y1, x2, y2), source_target_id=None,
                    extra={"target_size_m": CLASS_SIZE_M.get(cls, 0.15)}))
        return out

    def close(self) -> None:
        self._models.clear()
