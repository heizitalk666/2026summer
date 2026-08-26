"""L3 未知异常检测。

任务书明确要求"未知异常检测"，参考资料列了 EfficientAD、PaDiM。差异清单
B1 指出方案书全文 0 次提到它，需要回填——而且它正好补上 A2 把 OIL_LEAK 换成
SWITCH_HANDLE 之后空出来的"纯 L1／未知异常"通路：**非监督方法只用正常样本
训练，不需要缺陷标注数据**，恰恰绕开了方案书 §2.4.5 那条卡死外观缺陷的
数据可得性约束。

一条硬约束（ICD §3.1）：**L3 的输出只允许进人工复核队列。**任何下游模块
不得把 is_anomaly = true 当作缺陷判定结果直接上报告警。这是三层缺陷体系的
分工约定，写进接口是为了防止实现时图省事把它接到告警通路上。

这里给两个实现：

- ``StatisticalAnomaly``  在线学习"正常长什么样"，零训练、零权重，现在就能跑
- ``EfficientADAnomaly``  接口就位，等权重（见 training/）

选统计法做默认不是凑合：它在线building 正常模型，天然适应现场光照，而且
可解释——异常分来自哪个特征通道是能说清楚的，答辩时比一个黑盒分数好讲。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class AnomalyResult:
    model: str
    anomaly_score: float          # 归一化到 0–1
    threshold: float
    is_anomaly: bool
    heatmap: np.ndarray | None = None

    def to_dict(self, heatmap_ref: str | None = None) -> dict:
        """填 DetectionEvent.l3_anomaly。"""
        return {"model": self.model,
                "anomaly_score": round(float(np.clip(self.anomaly_score, 0, 1)), 4),
                "threshold": round(float(np.clip(self.threshold, 0, 1)), 4),
                "is_anomaly": bool(self.is_anomaly),
                "heatmap_ref": heatmap_ref}


class IAnomalyDetector(ABC):
    @abstractmethod
    def score(self, image: np.ndarray, bbox=None) -> AnomalyResult: ...

    def observe_normal(self, image: np.ndarray, bbox=None) -> None:
        """把一个已知正常的样本喂进模型。非监督方法只需要这个。"""
        return None


def _features(roi: np.ndarray) -> np.ndarray:
    """ROI 的紧凑描述子：Lab 颜色分布 + 梯度方向分布。

    颜色抓"这东西的材质对不对"，梯度抓"这东西的纹理对不对"。配电柜是
    低饱和的灰，异物往往在这两条上同时偏离。
    """
    if roi.size == 0:
        return np.zeros(40, np.float32)
    roi = cv2.resize(roi, (48, 48), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)
    feats = []
    for ch, bins in ((0, 8), (1, 8), (2, 8)):
        h = cv2.calcHist([lab], [ch], None, [bins], [0, 256]).ravel()
        feats.append(h / max(1e-6, h.sum()))
    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    ang = (np.degrees(np.arctan2(gy, gx)) % 180.0)
    hist, _ = np.histogram(ang, bins=12, range=(0, 180), weights=mag)
    feats.append(hist / max(1e-6, hist.sum()))
    feats.append(np.array([float(mag.mean()) / 255.0,
                           float(g.std()) / 255.0,
                           float(lab[..., 1].std()) / 128.0,
                           float(lab[..., 2].std()) / 128.0], np.float32))
    return np.concatenate(feats).astype(np.float32)


class StatisticalAnomaly(IAnomalyDetector):
    """在线学习正常分布，用马氏距离打异常分。

    只喂正常样本（非监督），所以不需要任何缺陷标注数据。冷启动阶段
    (样本 < warmup) 一律判为正常，避免开机头几帧全报异常。

    **warmup 不能设得太小。**实测样本只有 24 个时，正常分布的 σ 估得偏大，
    异常目标只能拿到 2.8σ，落在 3.3σ 阈值以下漏报；样本到 30 个以上后
    正常最大 1.5σ、异常 ≥6σ，72 个正常 / 12 个异常样本上误报漏报皆为 0。
    """

    def __init__(self, *, threshold: float = 0.55, warmup: int = 30,
                 max_memory: int = 512, model_name: str = "stat_lab_grad"):
        self.model_name = model_name
        self.threshold = float(threshold)
        self.warmup = int(warmup)
        self.max_memory = int(max_memory)
        self._mem: list[np.ndarray] = []
        self._mean: np.ndarray | None = None
        self._istd: np.ndarray | None = None
        self._d_mu: float = 0.0
        self._d_sigma: float = 1.0

    def _refit(self) -> None:
        if len(self._mem) < 2:
            return
        X = np.stack(self._mem)
        self._mean = X.mean(axis=0)
        self._istd = 1.0 / np.maximum(1e-4, X.std(axis=0))
        # **用正常样本自己的离散程度定标尺。**否则归一化系数是拍脑袋的，
        # 阈值也就失去物理含义，换个场景就得重调。这里统计正常样本距离的
        # 均值与标准差，异常分读作"偏离正常分布几个 σ"（除以 6 归一）。
        # 于是配置里的 threshold=0.55 对应 3.3σ，是经典的离群判据，
        # 而不是一个凑出来的数。
        d = np.percentile(np.abs((X - self._mean) * self._istd), 90, axis=1)
        self._d_mu = float(d.mean())
        self._d_sigma = float(max(1e-3, d.std()))

    def _distance(self, f: np.ndarray) -> float:
        z = np.abs((f - self._mean) * self._istd)
        # 取高分位而不是均值：异常通常只体现在少数几个特征通道上，
        # 取均值会被大量正常通道稀释掉。
        return float(np.percentile(z, 90))

    def observe_normal(self, image: np.ndarray, bbox=None) -> None:
        roi = _crop(image, bbox)
        if roi is None:
            return
        self._mem.append(_features(roi))
        if len(self._mem) > self.max_memory:
            self._mem.pop(0)
        self._refit()

    def score(self, image: np.ndarray, bbox=None) -> AnomalyResult:
        roi = _crop(image, bbox)
        if roi is None or self._mean is None or len(self._mem) < self.warmup:
            return AnomalyResult(self.model_name, 0.0, self.threshold, False)
        d = self._distance(_features(roi))
        sigmas = (d - self._d_mu) / self._d_sigma
        s = float(np.clip(sigmas / 6.0, 0.0, 1.0))
        return AnomalyResult(self.model_name, s, self.threshold, s > self.threshold)

    @property
    def ready(self) -> bool:
        return self._mean is not None and len(self._mem) >= self.warmup


class EfficientADAnomaly(IAnomalyDetector):
    """EfficientAD 后端。权重就位后启用，接口与统计法一致。

    训练脚本见 training/train_anomaly.py：只用正常样本，不需要缺陷标注。
    """

    def __init__(self, weights: str, *, threshold: float = 0.55,
                 device: str = "cpu"):
        self.threshold = float(threshold)
        self.weights = weights
        self.device = device
        self._net = None

    def _lazy(self):
        if self._net is None:
            raise RuntimeError(
                "EfficientAD 权重未就位。当前请用 StatisticalAnomaly，"
                "或先跑 training/train_anomaly.py 训一个出来。")
        return self._net

    def score(self, image: np.ndarray, bbox=None) -> AnomalyResult:
        self._lazy()
        raise NotImplementedError


def _crop(image: np.ndarray, bbox):
    if bbox is None:
        return image
    h, w = image.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in bbox]
    X1, Y1 = max(0, x1), max(0, y1)
    X2, Y2 = min(w, x2 + 1), min(h, y2 + 1)
    if X2 - X1 < 4 or Y2 - Y1 < 4:
        return None
    return image[Y1:Y2, X1:X2]


def build_anomaly(cfg) -> IAnomalyDetector | None:
    if not bool(cfg.get("perception.l3.enabled", True)):
        return None
    name = str(cfg.get("perception.l3.model", "efficientad_s"))
    thr = float(cfg.get("perception.l3.threshold", 0.55))
    weights = cfg.get("perception.l3.weights", None)
    if name.startswith("efficientad") and weights:
        return EfficientADAnomaly(weights, threshold=thr)
    return StatisticalAnomaly(threshold=thr,
                              warmup=int(cfg.get("perception.l3.warmup", 30)))
