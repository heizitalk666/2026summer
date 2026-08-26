"""合成检测器：模拟一个训练好的 YOLO 会输出什么。

**它替代的只是"框在哪、是什么类"这一步**，也就是 A 同学的模型还没训出来
之前的那一环。读数、质量评价、跟踪、状态机、证据包全部照常走真算法。

为什么这样分：检测框的位置精度对读数的影响是**二阶**的（读数算法自己会
用椭圆拟合重新定位表盘），而指针角度解算是**一阶**的。所以用真值加噪声
生成检测框，不会让精度指标失真；反过来如果连读数也用真值，那就全废了。

合成时刻意注入了三样真实检测器都有的毛病，让下游逻辑不至于活在理想世界：

- **框抖动**：中心与尺寸带高斯噪声，且噪声随目标变小而变大
- **漏检**：小目标漏检率高（像素太少），对应方案书 ≤2 % 的漏检率指标
- **误检**：偶尔在背景上凭空报一个框，这正是复核要消解掉的东西
"""
from __future__ import annotations

import numpy as np

from patrol.perception.detector.base import Detection, IDetector

#: 各类别的先验物理尺寸查表值。DetectionEvent.detections[].target_size_m
#: 明确写着"该类别的先验物理尺寸，查表得到，不是测出来的"。
CLASS_SIZE_M = {
    "PRESSURE_GAUGE": 0.15, "OIL_LEVEL_GAUGE": 0.12, "INDICATOR_LIGHT": 0.03,
    "SWITCH_HANDLE": 0.18, "INSULATOR_BREAK": 0.20, "OIL_LEAK": 0.25,
    "RUST_CORROSION": 0.20, "FOREIGN_OBJECT": 0.25, "DOOR_OPEN": 0.60,
    "CABLE_LOOSE": 0.10,
}


class SyntheticDetector(IDetector):
    def __init__(self, cfg, camera=None, seed: int = 0):
        self.cfg = cfg
        self.camera = camera
        self.rng = np.random.default_rng(seed)
        self._models = {
            "CRUISE": dict(cfg.get("perception.model.cruise")),
            "VERIFY": dict(cfg.get("perception.model.verify")),
        }
        self._nms = float(cfg.get("perception.model.nms_iou", 0.45))
        self._in_w = int(cfg.get("perception.model.input_w", 640))
        self._in_h = int(cfg.get("perception.model.input_h", 640))
        self._classes = set(cfg.get("mission.first_release_classes",
                                    list(CLASS_SIZE_M)))
        self._fp_rate = float(cfg.get("perception.synthetic.false_positive_rate", 0.02))

    def model_info(self, stage: str = "CRUISE") -> dict:
        m = self._models.get(stage, self._models["CRUISE"])
        return {"name": str(m.get("name", "yolo11s")),
                "input_w": self._in_w, "input_h": self._in_h,
                "quant": str(m.get("quant", "INT8")),
                "conf_threshold": float(m.get("conf_threshold", 0.25)),
                "nms_iou": self._nms}

    # ------------------------------------------------------------
    def infer(self, image: np.ndarray, *, conf_threshold: float,
              stage: str = "CRUISE") -> list[Detection]:
        if self.camera is None:
            return []
        H, W = image.shape[:2]
        out: list[Detection] = []
        for m in self.camera.last_targets():
            cls = m["defect_class"]
            if cls not in self._classes and not m.get("anomalous"):
                continue
            x1, y1, x2, y2 = [float(v) for v in m["bbox"]]
            bw, bh = x2 - x1, y2 - y1
            if bw < 3 or bh < 3:
                continue

            # 置信度：像素越多、越正对，模型越有把握。复核态用更大的模型，
            # 同一目标的置信度整体抬高——这正是 delta_conf 增益的来源。
            px = bw
            base = float(np.clip(0.16 + 0.55 * np.tanh(px / 90.0), 0.0, 0.97))
            base *= float(np.clip(0.55 + 0.45 * m.get("facing_cos", 1.0), 0.3, 1.0))
            base *= float(np.clip(1.0 - 0.6 * m.get("glare", 0.0), 0.25, 1.0))
            if stage == "VERIFY":
                base = float(np.clip(base * 1.28 + 0.12, 0.0, 0.985))
            conf = float(np.clip(base + self.rng.normal(0.0, 0.03), 0.0, 1.0))

            # 漏检：小目标漏得多，对应方案书 ≤2 % 的漏检率指标
            miss_p = float(np.clip(0.35 * np.exp(-px / 22.0), 0.0, 0.6))
            if self.rng.random() < miss_p or conf < conf_threshold:
                continue

            # 框抖动：噪声随目标变小而变大
            jit = max(0.6, 0.035 * px)
            dx, dy = self.rng.normal(0, jit, 2)
            ds = self.rng.normal(1.0, 0.02)
            cx, cy = (x1 + x2) / 2 + dx, (y1 + y2) / 2 + dy
            nw, nh = bw * ds, bh * ds
            bbox = (float(np.clip(cx - nw / 2, -nw, W + nw)),
                    float(np.clip(cy - nh / 2, -nh, H + nh)),
                    float(np.clip(cx + nw / 2, -nw, W + nw)),
                    float(np.clip(cy + nh / 2, -nh, H + nh)))
            out.append(Detection(defect_class=cls, confidence=round(conf, 4),
                                 bbox=bbox, source_target_id=m["target_id"],
                                 extra={"distance_m": m["distance_m"],
                                        "target_size_m": m["target_size_m"],
                                        "facing_cos": m.get("facing_cos", 1.0),
                                        "anomalous": bool(m.get("anomalous"))}))

        # 误检：偶尔在背景上凭空报一个。一级为了保召回把阈值压到 0.25，必然
        # 带来误报，复核把它们消解掉正是这套方案的立论所在。
        if self._fp_rate > 0 and self.rng.random() < self._fp_rate:
            cls = sorted(self._classes)[int(self.rng.integers(0, len(self._classes)))]
            w_ = float(self.rng.uniform(18, 60))
            x = float(self.rng.uniform(0, max(1, W - w_)))
            y = float(self.rng.uniform(0, max(1, H - w_)))
            c = float(np.clip(self.rng.uniform(conf_threshold, 0.55), 0, 1))
            out.append(Detection(defect_class=cls, confidence=round(c, 4),
                                 bbox=(x, y, x + w_, y + w_),
                                 source_target_id=None,
                                 extra={"distance_m": float(self.rng.uniform(3, 8)),
                                        "target_size_m": CLASS_SIZE_M.get(cls, 0.15),
                                        "false_positive": True}))
        return out
