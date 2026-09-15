"""导出后的 YOLO11 检测器：ONNX（本机）与 RKNN（RK3576 NPU）两个后端。

模型有两种形态，按输出个数自动识别：

- **整图**（1 个输出）：ultralytics 导出的 ONNX，输出 ``[1, 4 + nc, N]``，前 4 维是 letterbox
  画布上的 cx, cy, w, h，后 nc 维是各类分数（已过 sigmoid）。FP32/FP16 用它。
- **切头**（6 个输出）：在检测头解码之前截断，每个尺度各出一个框分支 ``[1, 64, H, W]``
  （DFL 分布的 logits）与一个类别分支 ``[1, nc, H, W]``（logits）。**INT8 必须用它**：
  整图把 0–1280 的坐标和 0–1 的分数拼在同一个输出里，INT8 一个缩放系数约 5 px 一档，
  分数全被量化成 0，实测一个框都出不来。DFL、锚点与 sigmoid 挪到 numpy 里做（split_to_raw）。

两种形态最后都还原成 ``[1, 4 + nc, N]``，预处理、解码、NMS 只写一份。

预处理与 ultralytics 的 predict 一致：等比缩放到 imgsz、居中补灰（114）、BGR→RGB。
ONNX 收 float32 NCHW（/255）；RKNN 模型在转换时写进了 mean=0 / std=255，
收 uint8 NHWC 原图，由 NPU 自己归一化——两边不能各自再除一次 255。

``torch`` 与 ``ultralytics`` 都不需要，板上只装 numpy、opencv 和 rknn-toolkit-lite2。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from patrol.perception.detector.base import Detection, IDetector
from patrol.perception.detector.synthetic import CLASS_SIZE_M

PAD_VALUE = 114


def letterbox(image: np.ndarray, size: int) -> tuple[np.ndarray, float, tuple[int, int]]:
    """BGR 原图 → 居中补灰的 size×size RGB uint8 画布。返回 (画布, 缩放比, (左补, 上补))。"""
    h, w = image.shape[:2]
    s = min(size / w, size / h)
    nw, nh = int(round(w * s)), int(round(h * s))
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR) if (nw, nh) != (w, h) else image
    px, py = (size - nw) // 2, (size - nh) // 2
    canvas = np.full((size, size, 3), PAD_VALUE, dtype=np.uint8)
    canvas[py:py + nh, px:px + nw] = resized
    return np.ascontiguousarray(canvas[:, :, ::-1]), s, (px, py)


def split_to_raw(outputs, size: int, reg_max: int = 16) -> np.ndarray:
    """切头模型的 6 个输出 → 与整图一致的 ``[1, 4 + nc, N]``。

    与 ultralytics 的 Detect 头逐项一致：DFL 在 reg_max 个区间上 softmax 取期望得到
    左上右下四个距离，锚点是格子中心，乘步长回到画布像素；类别过 sigmoid。
    尺度按特征图从大到小（步长 8 → 16 → 32）、格子按行优先排列，与 ONNX 里的顺序相同。
    """
    box = {}
    cls = {}
    for o in outputs:
        o = np.asarray(o, dtype=np.float32)
        if o.ndim == 3:
            o = o[None]
        (box if o.shape[1] == 4 * reg_max else cls)[o.shape[2]] = o[0]
    xywh, scores = [], []
    for h in sorted(box, reverse=True):
        b, c = box[h], cls[h]
        _, H, W = b.shape
        stride = size / H
        d = b.reshape(4, reg_max, H * W)
        d = np.exp(d - d.max(axis=1, keepdims=True))
        d = (d / d.sum(axis=1, keepdims=True) * np.arange(reg_max, dtype=np.float32)[None, :, None]).sum(axis=1)
        gy, gx = np.meshgrid(np.arange(H, dtype=np.float32) + 0.5, np.arange(W, dtype=np.float32) + 0.5,
                             indexing="ij")
        ax, ay = gx.ravel(), gy.ravel()
        x1, y1, x2, y2 = ax - d[0], ay - d[1], ax + d[2], ay + d[3]
        xywh.append(np.stack([(x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1]) * stride)
        scores.append(1.0 / (1.0 + np.exp(-c.reshape(c.shape[0], H * W))))
    return np.concatenate([np.concatenate(xywh, axis=1), np.concatenate(scores, axis=1)], axis=0)[None]


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float) -> list[int]:
    """贪心 NMS，boxes 为 xyxy。返回保留下标，按分数从高到低。"""
    order = np.argsort(-scores)
    keep: list[int] = []
    areas = np.maximum(0.0, boxes[:, 2] - boxes[:, 0]) * np.maximum(0.0, boxes[:, 3] - boxes[:, 1])
    while order.size:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        iou = inter / np.maximum(1e-9, areas[i] + areas[rest] - inter)
        order = rest[iou <= iou_thr]
    return keep


def decode(output: np.ndarray, *, conf_threshold: float, iou_threshold: float, scale: float,
           pad: tuple[int, int], image_wh: tuple[int, int], max_det: int = 300
           ) -> list[tuple[int, float, tuple[float, float, float, float]]]:
    """``[1, 4+nc, N]`` → [(类别下标, 置信度, 原图 xyxy)]。按类别分别做 NMS，与 ultralytics 默认一致。"""
    p = np.asarray(output, dtype=np.float32)
    if p.ndim == 3:
        p = p[0]
    if p.shape[0] < p.shape[1]:              # [4+nc, N] → [N, 4+nc]
        p = p.T
    cls_scores = p[:, 4:]
    cls = np.argmax(cls_scores, axis=1)
    conf = cls_scores[np.arange(len(cls)), cls]
    m = conf >= conf_threshold
    if not np.any(m):
        return []
    cxcywh, cls, conf = p[m, :4], cls[m], conf[m]
    boxes = np.empty_like(cxcywh)
    boxes[:, 0] = cxcywh[:, 0] - cxcywh[:, 2] / 2
    boxes[:, 1] = cxcywh[:, 1] - cxcywh[:, 3] / 2
    boxes[:, 2] = cxcywh[:, 0] + cxcywh[:, 2] / 2
    boxes[:, 3] = cxcywh[:, 1] + cxcywh[:, 3] / 2
    # 类别偏移：不同类的框挪到互不重叠的坐标区间，一次 NMS 等价于逐类 NMS
    offset = cls[:, None].astype(np.float32) * 10000.0
    keep = nms(boxes + offset, conf, iou_threshold)[:max_det]
    w, h = image_wh
    px, py = pad
    out = []
    for i in keep:
        x1 = float(np.clip((boxes[i, 0] - px) / scale, 0, w))
        y1 = float(np.clip((boxes[i, 1] - py) / scale, 0, h))
        x2 = float(np.clip((boxes[i, 2] - px) / scale, 0, w))
        y2 = float(np.clip((boxes[i, 3] - py) / scale, 0, h))
        if x2 - x1 >= 1.0 and y2 - y1 >= 1.0:
            out.append((int(cls[i]), float(conf[i]), (x1, y1, x2, y2)))
    return out


class _ExportedYolo(IDetector):
    """ONNX / RKNN 两个后端的公共部分。子类只实现 ``_load`` 与 ``_forward``。"""

    backend = "?"

    def __init__(self, cfg, section: str):
        self.cfg = cfg
        sec = cfg.get("perception.%s" % section)
        self._paths = {"CRUISE": Path(sec.get("weights_cruise")),
                       "VERIFY": Path(sec.get("weights_verify") or sec.get("weights_cruise"))}
        self._size = int(cfg.get("perception.model.input_w", 1280))
        self._nms = float(cfg.get("perception.model.nms_iou", 0.45))
        self._cfg_models = {"CRUISE": dict(cfg.get("perception.model.cruise")),
                            "VERIFY": dict(cfg.get("perception.model.verify"))}
        # DetectionEvent.model.quant 只允许 INT8 / FP16（ICD 按上板精度定义）。RKNN 模型报实际精度，
        # 由部署包按 rknn_report.json 的选择写进 quant_cruise / quant_verify；ONNX 与 .pt 后端一样报
        # perception.model 里登记的上板精度。
        self._quant = {s: str(sec.get("quant_%s" % s.lower(), self._cfg_models[s].get("quant", "INT8"))).upper()
                       for s in ("CRUISE", "VERIFY")}
        # 类别下标 → 名字。训练时的顺序就是这三个，ONNX 元数据里的 names 也是这个顺序
        self._names = list(sec.get("names", ["PRESSURE_GAUGE", "INDICATOR_LIGHT", "SWITCH_HANDLE"]))
        self._classes = set(cfg.get("mission.first_release_classes", list(CLASS_SIZE_M)))
        self._sessions: dict[str, object] = {}

    # ---- 子类实现
    def _load(self, path: Path):
        raise NotImplementedError

    def _forward(self, session, canvas_rgb: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    # ---- IDetector
    def _session(self, stage: str):
        key = stage if stage in self._paths else "CRUISE"
        if key not in self._sessions:
            p = self._paths[key]
            if not p.exists():
                raise FileNotFoundError(
                    "找不到 %s 模型 %s。先按 deploy/README-deploy.md 放好模型，"
                    "或把 perception.detector 改回 synthetic。" % (self.backend, p))
            self._sessions[key] = self._load(p)
        return self._sessions[key]

    def model_info(self, stage: str = "CRUISE") -> dict:
        c = self._cfg_models.get(stage, self._cfg_models["CRUISE"])
        return {"name": str(c.get("name", "yolo11s")), "input_w": self._size, "input_h": self._size,
                "quant": self._quant.get(stage, self._quant["CRUISE"]),
                "conf_threshold": float(c.get("conf_threshold", 0.25)),
                "nms_iou": self._nms}

    def infer(self, image: np.ndarray, *, conf_threshold: float, stage: str = "CRUISE") -> list[Detection]:
        sess = self._session(stage)
        canvas, scale, pad = letterbox(image, self._size)
        outs = self._forward(sess, canvas)
        raw = outs[0] if len(outs) == 1 else split_to_raw(outs, self._size)
        dets = decode(raw, conf_threshold=conf_threshold, iou_threshold=self._nms, scale=scale, pad=pad,
                      image_wh=(image.shape[1], image.shape[0]))
        out: list[Detection] = []
        for ci, conf, box in dets:
            name = self._names[ci] if ci < len(self._names) else str(ci)
            if name not in self._classes:
                continue
            out.append(Detection(defect_class=name, confidence=conf, bbox=box, source_target_id=None,
                                 extra={"target_size_m": CLASS_SIZE_M.get(name, 0.15)}))
        return out

    def close(self) -> None:
        for s in self._sessions.values():
            rel = getattr(s, "release", None)
            if callable(rel):
                rel()
        self._sessions.clear()


class OnnxYoloDetector(_ExportedYolo):
    """onnxruntime 后端。本机验证用，也可以在没有 NPU 的 Linux 上直接跑。"""

    backend = "ONNX"

    def __init__(self, cfg):
        super().__init__(cfg, "onnx")
        self._providers = list(cfg.get("perception.onnx.providers", ["CPUExecutionProvider"]))

    def _load(self, path: Path):
        import onnxruntime as ort
        avail = set(ort.get_available_providers())
        prov = [p for p in self._providers if p in avail] or ["CPUExecutionProvider"]
        return ort.InferenceSession(str(path), providers=prov)

    def _forward(self, session, canvas_rgb: np.ndarray) -> np.ndarray:
        x = canvas_rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        name = session.get_inputs()[0].name
        return session.run(None, {name: x})


class RknnYoloDetector(_ExportedYolo):
    """RK3576 NPU 后端（rknn-toolkit-lite2）。模型收 uint8 NHWC，归一化写在模型里。"""

    backend = "RKNN"

    def __init__(self, cfg):
        super().__init__(cfg, "rknn")
        self._core = str(cfg.get("perception.rknn.core_mask", "AUTO")).upper()

    def _load(self, path: Path):
        from rknnlite.api import RKNNLite
        r = RKNNLite()
        if r.load_rknn(str(path)) != 0:
            raise RuntimeError("load_rknn 失败：%s" % path)
        mask = getattr(RKNNLite, "NPU_CORE_%s" % self._core, RKNNLite.NPU_CORE_AUTO)
        if r.init_runtime(core_mask=mask) != 0:
            raise RuntimeError("init_runtime 失败：%s（是不是不在 RK3576 上？）" % path)
        return r

    def _forward(self, session, canvas_rgb: np.ndarray) -> np.ndarray:
        return session.inference(inputs=[canvas_rgb[None]], data_format=["nhwc"])
