"""OCR 不可用时的空实现。

**它返回空列表而不是抛异常，这是有意的。**互证通路缺席应当让结论**变保守**
（读数置信度打折、needs_human_review 抬起来），而不是让整条感知链崩掉。
`reason` 会如实进 meta.jsonl——事后查"为什么这一轮没有互证"时能查到。
"""
from __future__ import annotations

import numpy as np

from patrol.perception.ocr.base import IOcr, OcrLine


class DisabledOcr(IOcr):
    def __init__(self, reason: str = "未启用") -> None:
        self.reason = str(reason)

    def read(self, image: np.ndarray, bbox=None, *, margin: float = 0.12
             ) -> list[OcrLine]:
        return []

    def model_info(self) -> dict:
        return {"name": "disabled", "backend": "none", "offline": True,
                "reason": self.reason}

    @property
    def available(self) -> bool:
        return False
