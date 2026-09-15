"""导出后的 YOLO11（ONNX / RKNN 共用的预处理、解码、NMS）。

解码与 NMS 用构造的输出张量验，不依赖任何权重。逐框对照拿 ultralytics 跑**同一份 ONNX**
作参照：它对 .pt 用的是按步长补边的矩形输入（1920×1080 → 1280×736），与定长 1280×1280
的导出图不是同一个输入，小目标的置信度会差 0.1 以上，拿它当参照测不出解码写没写对。
需要 artifacts/*_ft/best.onnx，缺了就 skip。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from patrol.common.config import Config
from patrol.perception.detector.exported_yolo import decode, letterbox, nms

ROOT = Path(__file__).resolve().parents[1]


def _raw(rows, nc=3, n_pad=5):
    """rows: [(cx, cy, w, h, 类别, 分数)] → [1, 4+nc, N] 的原始输出。"""
    arr = np.zeros((len(rows) + n_pad, 4 + nc), np.float32)
    for i, (cx, cy, w, h, c, s) in enumerate(rows):
        arr[i, :4] = (cx, cy, w, h)
        arr[i, 4 + c] = s
    return arr.T[None]


def test_letterbox_centres_and_keeps_aspect():
    img = np.zeros((1080, 1920, 3), np.uint8)
    canvas, s, (px, py) = letterbox(img, 1280)
    assert canvas.shape == (1280, 1280, 3)
    assert s == pytest.approx(1280 / 1920)
    assert px == 0 and py == (1280 - 720) // 2
    assert canvas[0, 0].tolist() == [114, 114, 114]


def test_nms_keeps_highest_of_overlapping_boxes():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], np.float32)
    keep = nms(boxes, np.array([0.8, 0.9, 0.7], np.float32), 0.45)
    assert keep == [1, 2]


def test_decode_maps_back_to_original_image_and_is_class_aware():
    s, pad = 1280 / 1920, (0, 280)
    # 同一位置两个类别各一个框，外加一个低于阈值的框
    rows = [(640, 640, 100, 100, 0, 0.9), (640, 640, 100, 100, 1, 0.8), (200, 400, 40, 40, 2, 0.1)]
    dets = decode(_raw(rows), conf_threshold=0.25, iou_threshold=0.45, scale=s, pad=pad, image_wh=(1920, 1080))
    assert [d[0] for d in dets] == [0, 1]
    x1, y1, x2, y2 = dets[0][2]
    assert (x1, x2) == pytest.approx(((590 - 0) / s, (690 - 0) / s))
    assert (y1, y2) == pytest.approx(((590 - 280) / s, (690 - 280) / s))


def _frames():
    ev = sorted(ROOT.glob("evidence/*/*/cruise_raw.jpg"))[:3]
    return ev


@pytest.mark.parametrize("stage,weights", [("CRUISE", "cruise_ft"), ("VERIFY", "verify_ft")])
def test_onnx_backend_matches_ultralytics(tmp_path, stage, weights):
    pytest.importorskip("onnxruntime")
    ul = pytest.importorskip("ultralytics")
    onnx_p = ROOT / "artifacts" / weights / "best.onnx"
    frames = _frames()
    if not (onnx_p.exists() and frames):
        pytest.skip("需要 %s 与 evidence 里的巡航原图" % onnx_p)
    from patrol.common.imio import imread
    from patrol.perception.detector.exported_yolo import OnnxYoloDetector
    cfg = Config.load(overrides={"logging": {"dir": str(tmp_path)},
                                 "perception": {"onnx": {"providers": ["CPUExecutionProvider"]}}})
    det = OnnxYoloDetector(cfg)
    ref = ul.YOLO(str(onnx_p), task="detect")
    conf = 0.25
    matched = total = 0
    for f in frames:
        img = imread(f)
        ours = det.infer(img, conf_threshold=conf, stage=stage)
        theirs = ref.predict(img, imgsz=1280, conf=conf, iou=0.45, device="cpu", verbose=False)[0]
        for b in theirs.boxes:
            total += 1
            tb = [float(v) for v in b.xyxy[0]]
            for d in ours:
                ix = max(0.0, min(tb[2], d.bbox[2]) - max(tb[0], d.bbox[0]))
                iy = max(0.0, min(tb[3], d.bbox[3]) - max(tb[1], d.bbox[1]))
                inter = ix * iy
                union = (tb[2] - tb[0]) * (tb[3] - tb[1]) + d.width * (d.bbox[3] - d.bbox[1]) - inter
                if union > 0 and inter / union >= 0.98 and abs(d.confidence - float(b.conf[0])) < 0.005:
                    matched += 1
                    break
    det.close()
    if total == 0:
        pytest.skip("这几帧 ultralytics 一个框都没出，无从对照")
    assert matched == total, "ONNX 后端与 ultralytics 逐框对不上：%d/%d" % (matched, total)
