"""检测器抽象。

和驱动层同样的思路：业务代码只依赖这个接口，具体用合成检测器、YOLO 还是
将来的 RKNN，由 configs 里的 perception.detector 决定。

代码先行、模型后训：现在默认走 synthetic（场景真值加噪，模拟一个训练好的
YOLO 会输出什么），全链路先跑通；等公开数据集训出权重再把 detector 改成
yolo，上层一行不改。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Detection:
    """一个检出目标。字段对应 DetectionEvent.detections[]。"""

    defect_class: str
    confidence: float
    bbox: tuple[float, float, float, float]     # [x1, y1, x2, y2] px
    track_id: int = -1
    #: 桩用来把检出与场景目标对应起来，真机上恒为 None。
    #: **只用于读数所需的先验查表与测试打分，不参与检测本身。**
    source_target_id: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2.0

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2.0

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]


class IDetector(ABC):
    @abstractmethod
    def infer(self, image: np.ndarray, *, conf_threshold: float,
              stage: str = "CRUISE") -> list[Detection]:
        """一帧推理。stage 决定用巡航模型还是复核模型。"""

    @abstractmethod
    def model_info(self, stage: str = "CRUISE") -> dict:
        """填 DetectionEvent.model 用：name / input_w / input_h / quant / nms_iou。"""

    def close(self) -> None:
        return None


def build_detector(cfg, camera=None) -> IDetector:
    """按配置构造。这是检测器的唯一分支点，与 drivers/factory 同理。"""
    kind = str(cfg.get("perception.detector", "synthetic")).lower()
    if kind == "synthetic":
        from patrol.perception.detector.synthetic import SyntheticDetector
        return SyntheticDetector(cfg, camera)
    if kind == "yolo":
        from patrol.perception.detector.yolo import YoloDetector
        return YoloDetector(cfg)
    raise ValueError("perception.detector 只能是 synthetic 或 yolo，收到 %r" % kind)
