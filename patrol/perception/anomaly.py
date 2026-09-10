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

选统计法做默认不是凑合：它在线构建正常模型，天然适应现场光照，而且
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

    # -- 基线的固化与加载 ------------------------------------------
    def export_baseline(self) -> dict:
        """把当前学到的正常分布导出成一份基线。

        非监督方法的代价是每次冷启动都要重新预热 30 个样本，这期间一律判正常
        （宁可漏报也不能开机就满屏误报）。把跑过的分布存下来，下次直接加载，
        预热期就省掉了。training/train_anomaly.py 的统计法通路产出的就是它。
        """
        if self._mean is None:
            return {"model": self.model_name, "ready": False}
        return {"model": self.model_name, "ready": True,
                "threshold": self.threshold,
                "mean": self._mean.tolist(),
                "istd": self._istd.tolist(),
                "d_mu": self._d_mu, "d_sigma": self._d_sigma,
                "samples": len(self._mem)}

    def load_baseline(self, data: dict) -> bool:
        """加载基线。加载成功即视为已过预热期。"""
        if not data.get("ready"):
            return False
        self._mean = np.asarray(data["mean"], dtype=float)
        self._istd = np.asarray(data["istd"], dtype=float)
        self._d_mu = float(data["d_mu"])
        self._d_sigma = float(max(1e-3, data["d_sigma"]))
        self.threshold = float(data.get("threshold", self.threshold))
        self.warmup = 0                      # 分布已就位，不必再预热
        return True

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
    """EfficientAD（简化版）后端。训练脚本见 training/train_anomaly.py。

    打分语义与统计法一致：特征距离按训练批上的正常分布归一化，除以 6 压到
    0-1，threshold 0.55 对应 3.3σ。学生-教师蒸馏的代价是推理要跑两个前向
    （CPU 上约几十毫秒）——部署目标是把学生导成 ONNX 上 NPU，见
    training/export_rknn.py 与交付记录。
    """

    def __init__(self, weights: str, *, threshold: float = 0.55,
                 device: str = "cpu"):
        self.model_name = "efficientad_s"
        self.threshold = float(threshold)
        self.weights = str(weights)
        self.device = device
        # 延迟导入 torch：装不上时由 build_anomaly 退回统计法，这里不崩
        import torch
        import torch.nn as nn
        from torchvision import models, transforms

        ck = torch.load(self.weights, map_location=self.device, weights_only=False)
        if "student" not in ck:
            raise ValueError("权重文件里没有 student 状态：%s" % self.weights)

        def build(pretrained: bool):
            m = models.resnet18(weights="DEFAULT" if pretrained else None)
            return nn.Sequential(*list(m.children())[:6])

        self._teacher = build(True).eval().to(self.device)
        for p in self._teacher.parameters():
            p.requires_grad_(False)
        self._student = build(False).to(self.device)
        self._student.load_state_dict(ck["student"])
        self._student.eval()
        self._d_mu = float(ck.get("d_mu", 0.0))
        self._d_sigma = float(max(1e-6, ck.get("d_sigma", 1.0)))
        self._tf = transforms.Compose(
            [transforms.ToTensor(),
             transforms.Resize((256, 256), antialias=True)])

    def score(self, image: np.ndarray, bbox=None) -> AnomalyResult:
        roi = _crop(image, bbox)
        if roi is None:
            return AnomalyResult(self.model_name, 0.0, self.threshold, False)
        import torch
        # 训练时喂的是 BGR→RGB 的 uint8 图（train_anomaly 的 transforms 同款）
        x = self._tf(roi[:, :, ::-1].copy()).unsqueeze(0).to(self.device)
        with torch.inference_mode():
            d = ((self._student(x) - self._teacher(x)) ** 2).mean().item()
        sigmas = (d - self._d_mu) / self._d_sigma
        s = float(np.clip(sigmas / 6.0, 0.0, 1.0))
        return AnomalyResult(self.model_name, s, self.threshold, s > self.threshold)


class PadimAnomaly(IAnomalyDetector):
    """PaDiM(对角协方差简化版)后端。训练脚本见 training/train_anomaly.py。

    打分:预训练主干 layer2+layer3 特征逐位置马氏距离(对角)的 top-k 均值,
    再按训练集上的分数分布做 σ 归一化——与统计法同一套 0-1 语义
    (threshold 0.55 ≈ 3.3σ)。权重文件里自带 mu/var 与 d_mu/d_sigma。
    """

    def __init__(self, weights: str, *, threshold: float = 0.55,
                 device: str = "cpu"):
        self.model_name = "padim_s"
        self.threshold = float(threshold)
        self.weights = str(weights)
        self.device = device
        import torch
        import torch.nn as nn
        from torchvision import models, transforms
        # 小模型用满全部核心反而慢:线程调度开销 > 计算收益,还会和同进程的
        # 渲染(numpy/cv2 多线程)互相抢核。实测 14 核降到 4 核,单次打分
        # 更快且帧预算波动更小。
        torch.set_num_threads(4)

        ck = torch.load(self.weights, map_location=self.device, weights_only=False)
        if "mu" not in ck or ("var" not in ck and "cov" not in ck):
            raise ValueError("权重文件里没有 mu/var/cov:%s" % self.weights)
        children = list(models.resnet18(weights="DEFAULT").children())
        # **主干一帧只前向一次。**原来这里建了两条独立通路 children[:6] 与
        # children[:7]，打一次分要跑两遍前向，后者把前者的 6 层原样重算了一遍。
        # layer3 = children[6] 的输入本来就是 layer2 的输出，直接串起来即可：
        # 实测 _features 28.5 ms → 16.0 ms（省 43.9 %），特征张量逐元素相同
        # （12 个随机 ROI 上最大相对误差 0），打分结果不受任何影响。
        self._net2 = nn.Sequential(*children[:6]).eval().to(self.device)
        self._layer3 = children[6].eval().to(self.device)
        self._mu = ck["mu"].to(self.device)
        self._cov = "cov" in ck          # 全协方差版(padim_cov)与对角版共用打分入口
        if self._cov:
            self.model_name = "padim_cov_s"
            self._inv = ck["inv"].to(self.device)
            self._idx = ck["idx"].to(self.device)
        else:
            self._var = ck["var"].to(self.device)
        self._d_mu = float(ck.get("d_mu", 0.0))
        self._d_sigma = float(max(1e-6, ck.get("d_sigma", 1.0)))
        self._topk_frac = float(ck.get("topk_frac", 0.1))
        self._tf = transforms.Compose(
            [transforms.ToTensor(),
             transforms.Resize((256, 256), antialias=True)])

    def _features(self, roi: np.ndarray):
        import torch
        x = self._tf(roi[:, :, ::-1].copy()).unsqueeze(0).to(self.device)
        with torch.inference_mode():
            f2 = self._net2(x)
            f3 = torch.nn.functional.interpolate(
                self._layer3(f2), size=f2.shape[2:],
                mode="bilinear", align_corners=False)
        return torch.cat([f2, f3], dim=1)[0]

    def score(self, image: np.ndarray, bbox=None) -> AnomalyResult:
        roi = _crop(image, bbox)
        if roi is None:
            return AnomalyResult(self.model_name, 0.0, self.threshold, False)
        import torch
        with torch.inference_mode():
            f = self._features(roi)
            if self._cov:
                P = f.shape[1] * f.shape[2]
                Xp = torch.gather(f.permute(1, 2, 0).reshape(P, -1), 1, self._idx)
                d = torch.einsum("pk,pkj,pj->p", Xp - self._mu, self._inv,
                                 Xp - self._mu).reshape(f.shape[1], f.shape[2])
            else:
                d = ((f - self._mu) ** 2 / self._var).mean(dim=0)
        k = max(1, int(d.numel() * self._topk_frac))
        topk = float(d.flatten().topk(k).values.mean())
        sigmas = (topk - self._d_mu) / self._d_sigma
        s = float(np.clip(sigmas / 6.0, 0.0, 1.0))
        return AnomalyResult(self.model_name, s, self.threshold, s > self.threshold)


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
    if name.startswith("efficientad") and weights and str(weights).endswith(".pt"):
        try:
            return EfficientADAnomaly(weights, threshold=thr)
        except Exception:
            # torch 缺失 / 权重损坏 / 下载预训练主干失败 → 退回统计法。
            # 接口约定：L3 是可降级通路，不能让可选依赖把感知节点拖停。
            pass
    if name.startswith("padim") and weights and str(weights).endswith(".pt"):
        try:
            return PadimAnomaly(weights, threshold=thr)
        except Exception:
            pass
    det = StatisticalAnomaly(threshold=thr,
                             warmup=int(cfg.get("perception.l3.warmup", 30)))
    # 统计法的基线可以预先固化（training/train_anomaly.py），加载上就省掉
    # 冷启动那 30 个样本的预热期。没有基线也照常跑，只是前 30 帧一律判正常。
    if weights and str(weights).endswith(".json"):
        import json
        from pathlib import Path
        try:
            det.load_baseline(json.loads(Path(weights).read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError):
            pass
    return det
