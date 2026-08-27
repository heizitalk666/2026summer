"""OCR 抽象层。第二条**独立**的读数通路。

和检测器、驱动同一套思路：上层只依赖这个接口，用哪个 OCR 引擎由
`perception.ocr.backend` 决定。

**为什么值得单独拉一个模型进来。**几何法读指针是从像素里量角度，量得再准
也回答不了一个问题：*这块表的量程到底是多少*。量程来自标定表——而标定表是
人录的，录错了、或者车停错了航点看错了柜子，几何法算出的 0.85 MPa 会是一个
**看起来完全正常**的错值，没有任何征兆。

表盘上其实印着答案：刻度数字和单位就画在表面上。OCR 把它们读出来，就得到
一份**与标定表相互独立**的量程与单位。两边对得上，读数才可信；对不上，说明
不是表读错了就是站错了地方，这时候正确的动作是交给人，而不是报一个数。

开关把手同理：几何法量把手朝向，OCR 读位置指示牌上的 ON/OFF，两条完全不同
的通路给同一个答案。方案书表 2-2 要求开关分合位正确率 ≥ 99 %，靠单一通路
很难说清"凭什么"，两路互证才有话可讲。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class OcrLine:
    """一行识别结果。bbox 是**原图**坐标，便于直接画到预览窗口上。"""

    text: str
    conf: float
    bbox: tuple[float, float, float, float]

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2.0

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2.0


class IOcr(ABC):
    """文字识别。实现必须是**离线**的——巡检车上没有外网。"""

    @abstractmethod
    def read(self, image: np.ndarray,
             bbox: tuple[float, float, float, float] | None = None,
             *, margin: float = 0.12) -> list[OcrLine]:
        """识别 ROI 内的文字。bbox 为 None 时读整图。"""

    @abstractmethod
    def model_info(self) -> dict:
        """填 meta.jsonl 与答辩用：name / backend / offline。"""

    @property
    def available(self) -> bool:
        return True

    def close(self) -> None:
        return None


def crop(image: np.ndarray, bbox, margin: float = 0.12
         ) -> tuple[np.ndarray, float, float] | None:
    """按 bbox 裁 ROI（带外扩），返回 (patch, x0, y0)。

    外扩是必要的：单位字符串画在表盘中下方、开关的 ON/OFF 牌在底边，
    检测框贴着目标边缘时它们正好被切掉一半。
    """
    if bbox is None:
        return image, 0.0, 0.0
    h, w = image.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in bbox)
    bw, bh = x2 - x1, y2 - y1
    if bw < 4 or bh < 4:
        return None
    mx, my = bw * margin, bh * margin
    X1 = max(0, int(round(x1 - mx)))
    Y1 = max(0, int(round(y1 - my)))
    X2 = min(w, int(round(x2 + mx)))
    Y2 = min(h, int(round(y2 + my)))
    if X2 - X1 < 4 or Y2 - Y1 < 4:
        return None
    return image[Y1:Y2, X1:X2], float(X1), float(Y1)


def build_ocr(cfg) -> IOcr:
    """按配置构造。**这是 OCR 的唯一分支点**，与 drivers/factory 同理。

    引擎装不上时不抛异常而是退回 DisabledOcr：OCR 是互证通路，缺了它读数
    通路照样能出结果（只是置信度低一档、需要人工复核的比例上升）。让一个
    可选依赖把整台车的感知拖停是不能接受的。

    **工厂本身不写日志、不建文件**（建 logger 会顺手落一个日志文件，工厂被
    测试反复调用时就是一地垃圾）。降级原因存在 `DisabledOcr.reason` 里，由
    调用方在自己的 logger 上报出来。
    """
    from patrol.perception.ocr.disabled import DisabledOcr

    kind = str(cfg.get("perception.ocr.backend", "rapid")).lower()
    if kind in ("off", "none", "disabled"):
        return DisabledOcr("配置关闭")
    if kind != "rapid":
        raise ValueError("perception.ocr.backend 只能是 rapid 或 off，收到 %r" % kind)
    try:
        from patrol.perception.ocr.rapid import RapidOcr
        return RapidOcr(cfg)
    except Exception as exc:                                   # noqa: BLE001
        return DisabledOcr("引擎不可用：%s" % exc)
