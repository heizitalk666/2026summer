"""分割抽象层。L2 读数的第二种实现路线。

**为什么读数这一路要有两种实现。**

现在几何法（reading/pointer.py）是准的：椭圆拟合定盘、极坐标展开找针、
逐环角质心细化，实测线性度 0.34 % FS。它准，是因为它把"表盘长什么样"这件
事写死在了算法里——圆形表圈、深色细针、外圈刻度、针从圆心伸出。

这些假设在本项目的渲染场景里全都成立，在真实配电室里大部分时候也成立。
问题是**它们不成立的时候，几何法不是精度下降，是直接失效**：方形表、
液晶叠加指针、指针与背景同色、玻璃反光盖住半个盘面。而这类表在一个真实
配电室里总有几块。

学习出来的分割不需要这些假设——它从像素直接给出"这块是不是针"。代价是
需要标注，而标注恰恰是这类项目最难拿到的东西。合成数据集把这个代价降到
了零（见 training/gen_synthetic.py）。

所以两条路的分工是清楚的：**几何法是主力，学习法是兜底与扩展**。接口
一样，配置切换，谁在什么情况下更好由 tools/bench_models.py 用数据说话，
不靠拍脑袋。

注意分割出来的掩膜**不直接给读数**——它接回几何法那条链的中段：
极坐标展开、亚度级角质心细化、180° 歧义消解这些都照旧复用。分割替换掉的
只是"哪些像素是针"这一步，也就是几何法里假设最强的那一步。这是级联，
不是二选一。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class GaugeMask:
    """一次分割的结果。坐标系是**传进来的那张 patch**，不是原图。

    `needle` 是概率图而不是二值图：下游 `_needle_angle` 做的是加权角质心，
    概率的软边缘正是亚度级精度的来源。二值化会把这部分信息丢掉——实测
    硬阈值让角度误差涨了两倍多。
    """

    needle: np.ndarray                    # float32, [0,1], 与 patch 同尺寸
    face: np.ndarray | None = None
    ticks: np.ndarray | None = None
    model: str = "?"

    @property
    def ok(self) -> bool:
        return (self.needle is not None and self.needle.size > 0
                and float(self.needle.max()) > 0.15)


class ISegmenter(ABC):
    @abstractmethod
    def segment(self, patch: np.ndarray) -> GaugeMask | None:
        """对一块表盘 ROI 做像素级分割。拿不准就返回 None，让几何法接手。"""

    @abstractmethod
    def model_info(self) -> dict:
        ...

    def close(self) -> None:
        return None


def build_segmenter(cfg):
    """按配置构造。**这是分割的唯一分支点。**

    默认 `builtin` —— 不加载任何权重，读数走几何法那条链（也就是现状）。
    这是有意的默认：权重没训之前，一个没学过东西的模型只会让读数变差，
    而"默认配置跑出来就是最好的结果"这件事对交付很重要。
    """
    kind = str(cfg.get("perception.segmenter.backend", "builtin")).lower()
    if kind in ("builtin", "off", "none", "geometric"):
        return None
    if kind == "npz":
        from patrol.perception.segment.pixel import NpzSegmenter
        return NpzSegmenter(cfg)
    if kind == "onnx":
        from patrol.perception.segment.onnx_seg import OnnxSegmenter
        return OnnxSegmenter(cfg)
    raise ValueError(
        "perception.segmenter.backend 只能是 builtin / npz / onnx，收到 %r" % kind)
