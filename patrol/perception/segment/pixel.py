"""逐像素分类器：能在**这台机器上**真训出来的分割模型。

选它不是因为它比 U-Net 强，而是因为它**能真的跑完一遍"造数据 → 训练 →
导出 → 推理 → 接进读数链"**。这条链路走通了，换成 U-Net 只是把这一个文件
换掉；走不通的话，再大的模型也只是纸面上的。

三个约束逼出了这个选择：

- `download.pytorch.org` 和 `huggingface.co` 在这台机器上不可达，
  torchvision 的预训练权重拿不到
- torch 的 wheel 有 527 MB，组里同学各自的笔记本上装一遍代价不小
- 而验收要的是"多模型协同"这条链路成立，不是某个模型的 SOTA 指标

于是：**特征工程 + 逻辑回归**，纯 numpy，权重是一个几十 KB 的 .npz。
训练在 training/train_segmenter.py，几秒钟跑完。

特征取的是每个像素的**局部外观**，刻意不含"离圆心多远"这类几何量：
带上半径的话模型会直接学成"0.76R 以外就是刻度"，那就退化成几何法的
翻版了，遇到方形表照样失效——而学习法的全部意义就在于不依赖那些假设。

    f = [灰度, 局部均值差, 梯度幅值, 梯度方向的 sin/cos, 局部方差, 1]

梯度方向进特征是关键的一维：针和刻度都是细长条，但**针的方向沿半径、
刻度的方向也沿半径**，两者靠方向分不开——靠的是"针贯穿而刻度只在外圈"，
这个由下游的极坐标覆盖率扫描来判（见 reading/pointer.py）。分割在这里
只负责把"细长深色条"整体从盘面上摘出来，摘得干净就够了。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from patrol.perception.segment.base import GaugeMask, ISegmenter

#: 统一工作分辨率。**特征里有固定长度的线状核，尺度必须先归一化**，
#: 否则同一块表在 60 px 和 200 px 下算出来的"长划痕响应"完全不是一回事。
#: 与 reading/pointer.py 的 WORK=256 同理，这里取一半够用且快四倍。
WORK = 128
#: 特征维数，与 features() 的输出一致。改了要同步重训。
N_FEAT = 9
#: 类别顺序与 scene/gauges.SEG_LABELS 一致：背景/面/针/刻度
N_CLASS = 4

def _stroke_stats(dark: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """每个深色连通域的**长度**与**细长度**，逐像素铺回去。

    **这两维是针与刻度的唯一分界。**两者都是从圆心往外的深色细条，亮度、
    宽度、梯度方向全都一样，逐像素的局部外观根本分不开——第一版只有局部
    特征，针的验证 IoU 只有 0.005，等于全判成了刻度或盘面。

    真正的区别是**长度**：针从圆心伸到 0.80R，刻度只有 0.12R。

    第二版试过形态学：一根长线状结构元做开运算，短划痕被整根抹掉。想法对，
    但**朝向数不够**——开运算要求结构元整根落在暗区内，31 px 长、2 px 宽的
    针只容得下约 3.7° 的朝向误差，要覆盖 180° 得算近五十个朝向，比后面整个
    分类器还贵。取六个朝向的结果是针的响应比刻度还弱（0.003 vs 0.017），
    这一维直接反了。

    连通域没有这个问题：它天然与朝向无关，一次 connectedComponentsWithStats
    就出所有分量的外接框，长度取对角线。代价是可能粘连（针连着轴帽、刻度
    连着刻度环），但这只是一维特征，不是判决——粘连让特征变模糊，不会让它
    说反话。
    """
    d8 = np.clip(dark * 255.0, 0, 255).astype(np.uint8)
    # Otsu 在"暗"这一侧定阈值：盘面亮、笔画暗，双峰分得很开
    _thr, bw = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    n, lab, stats, _c = cv2.connectedComponentsWithStats(bw, 8)
    if n <= 1:
        z = np.zeros(dark.shape, np.float32)
        return z, z
    w = stats[:, cv2.CC_STAT_WIDTH].astype(np.float32)
    h = stats[:, cv2.CC_STAT_HEIGHT].astype(np.float32)
    area = stats[:, cv2.CC_STAT_AREA].astype(np.float32)
    diag = np.hypot(w, h) / float(WORK)                 # 归一到 [0,1]
    diag[0] = 0.0                                        # 0 号是背景
    # 细长度 = 1 − 填充率。笔画的外接框大部分是空的，块状区域填得满
    fill = area / np.maximum(1.0, w * h)
    thin = np.clip(1.0 - fill, 0.0, 1.0)
    thin[0] = 0.0
    return diag[lab].astype(np.float32), thin[lab].astype(np.float32)


def prepare(patch: np.ndarray) -> np.ndarray:
    """把 ROI 归一到工作分辨率。训练与推理都要经过这一步。"""
    return cv2.resize(patch, (WORK, WORK),
                      interpolation=cv2.INTER_AREA if max(patch.shape[:2]) > WORK
                      else cv2.INTER_CUBIC)


def features(patch: np.ndarray) -> np.ndarray:
    """(WORK, WORK, N_FEAT) 的逐像素特征。训练与推理**必须调同一个函数**。

    特征提取一旦在两处各写一遍，迟早会漂；漂了之后模型在训练集上完美、
    上线全错，而且查起来极其痛苦——因为两边的代码看起来都对。

    刻意**不含“离圆心多远”这类几何量**：带上半径的话模型会直接学成
    “0.76R 以外就是刻度”，退化成几何法的翻版，遇到方形表照样失效——
    而学习法的全部意义就在于不依赖那些假设。
    """
    q = prepare(patch)
    g = (cv2.cvtColor(q, cv2.COLOR_BGR2GRAY) if q.ndim == 3
         else q).astype(np.float32) / 255.0
    dark = 1.0 - g
    blur = cv2.GaussianBlur(g, (0, 0), 2.0)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    ang = np.arctan2(gy, gx)
    mean = cv2.blur(g, (9, 9))
    var = cv2.blur(g * g, (9, 9)) - mean * mean
    length, thin = _stroke_stats(dark)
    return np.stack([
        g,                                  # 亮度：针和刻度都比盘面暗
        g - blur,                           # 局部对比：抗整体明暗变化
        np.clip(mag, 0.0, 4.0),             # 边缘强度
        np.sin(2.0 * ang) * mag,            # 方向（取 2θ，方向 ±180° 等价）
        np.cos(2.0 * ang) * mag,
        np.clip(var, 0.0, 0.25) * 4.0,      # 局部方差：刻度带比盘面花
        length,                             # 所属笔画有多长 → 针 vs 刻度
        thin,                               # 有多细长 → 笔画 vs 块状
        np.ones_like(g),                    # 偏置
    ], axis=-1).astype(np.float32)


def softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / np.clip(e.sum(axis=-1, keepdims=True), 1e-9, None)


class NpzSegmenter(ISegmenter):
    """加载 .npz 权重的逐像素分类器。

    权重缺席时**构造就抛异常**，由 build_segmenter 的调用方决定怎么办。
    悄悄退回随机权重是最坏的选择：它会输出一张看似有内容的掩膜，把读数
    带偏，而日志里一句话都没有。
    """

    def __init__(self, cfg=None, weights: str | Path | None = None) -> None:
        g = (lambda k, d: d) if cfg is None else (lambda k, d: cfg.get(k, d))
        path = Path(weights or g("perception.segmenter.weights",
                                 "training/runs/seg/pixel.npz"))
        if not path.exists():
            raise FileNotFoundError(
                "分割权重不存在：%s（先跑 python -m training.train_segmenter）" % path)
        d = np.load(path)
        self.W = d["W"].astype(np.float32)          # (N_FEAT, N_CLASS)
        self.mu = d["mu"].astype(np.float32)
        self.sigma = d["sigma"].astype(np.float32)
        if self.W.shape != (N_FEAT, N_CLASS):
            raise ValueError("权重形状 %s 与当前特征定义 (%d, %d) 对不上，需重训"
                             % (self.W.shape, N_FEAT, N_CLASS))
        self.path = str(path)
        self.min_side = int(g("perception.segmenter.min_side_px", 40))
        self.iou = float(d["val_iou"]) if "val_iou" in d else float("nan")

    def segment(self, patch: np.ndarray) -> GaugeMask | None:
        if patch is None or patch.size == 0:
            return None
        if min(patch.shape[:2]) < self.min_side:
            return None                     # 太小时逐像素分类没有意义
        f = features(patch)
        z = ((f - self.mu) / self.sigma).reshape(-1, N_FEAT) @ self.W
        p = softmax(z).reshape(WORK, WORK, N_CLASS)
        # 概率图放回 ROI 尺寸。**用线性插值而不是最近邻**：下游做的是加权
        # 角质心，软边缘正是亚度级精度的来源（见 GaugeMask 的说明）。
        h, w = patch.shape[:2]
        p = cv2.resize(p, (w, h), interpolation=cv2.INTER_LINEAR)
        return GaugeMask(needle=p[..., 2].astype(np.float32),
                         face=p[..., 1].astype(np.float32),
                         ticks=p[..., 3].astype(np.float32),
                         model="pixel-lr")

    def model_info(self) -> dict:
        return {"name": "pixel-lr", "backend": "numpy", "offline": True,
                "weights": self.path, "n_feat": N_FEAT, "val_iou": self.iou}
