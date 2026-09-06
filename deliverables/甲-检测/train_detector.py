#!/usr/bin/env python3
"""检测模型训练。

    python -m training.train_detector --stage cruise
    python -m training.train_detector --stage verify

**两级级联的分工是整套方案的立论**，所以两个阶段不能用同一套超参：

| | 巡航态 | 复核态 |
|---|---|---|
| 模型 | YOLO11s | YOLO11m |
| 目标 | **不漏**（漏检率 ≤2 %） | **判准** |
| 置信度阈值 | 0.25 | 0.60 |
| 时延 | ≤100 ms/帧 | 车已停稳，放宽 |

一级把阈值压到 0.25 保召回，必然带来误报；复核把误报消解掉。往高召回调的
代价是复核次数上升，而复核预算 N_max 是有限的——这两件事的平衡点在
`configs/system.yaml` 的 `perception.model` 与 `mission.budget` 里，
训练时把召回调上去，运行时靠抑制规则和预算控住复核次数。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

STAGES = {
    "cruise": dict(model="yolo11s.pt", epochs=120, imgsz=640, batch=4,
                   conf=0.25, iou=0.5, name="cruise", workers=2,
                   note="保召回：conf 压到 0.25，NMS iou 放松，宁可多报不可漏报"),
    "verify": dict(model="yolo11m.pt", epochs=150, imgsz=640, batch=4,
                   conf=0.60, iou=0.45, name="verify", workers=2,
                   note="判准：conf 提到 0.60，车已停稳可用更大的模型"),
}


def main() -> int:
    ap = argparse.ArgumentParser(description="检测模型训练")
    ap.add_argument("--stage", choices=sorted(STAGES), required=True)
    ap.add_argument("--data", default=str(ROOT / "datasets" / "yolo" / "data.yaml"))
    ap.add_argument("--device", default="cpu", help="cpu 或 0/0,1")
    ap.add_argument("--epochs", type=int, default=None)
    ap.add_argument("--project", default=str(ROOT / "runs"))
    ap.add_argument("--weights", default=None,
                    help="从指定权重微调（默认公开 yolo11s/11m.pt 从头训）")
    ap.add_argument("--name", default=None,
                    help="输出目录名（默认 stage 名；微调另存请用它，免得盖掉原权重）")
    ap.add_argument("--resume", action="store_true",
                    help="从 runs/<stage>/weights/last.pt 断点续训")
    a = ap.parse_args()

    cfg = dict(STAGES[a.stage])
    if a.epochs:
        cfg["epochs"] = a.epochs
    cfg["model"] = a.weights or cfg["model"]
    cfg["name"] = a.name or cfg["name"]
    data = Path(a.data)
    if not data.exists():
        print("找不到数据集描述 %s" % data)
        print("先跑：python -m training.prepare_dataset --list")
        return 1

    try:
        from ultralytics import YOLO
    except ImportError:
        print("没装 ultralytics。跑：pip install -r requirements-yolo.txt")
        return 1

    print("阶段 %s：%s" % (a.stage, cfg["note"]))
    if a.resume:
        last = Path(a.project) / cfg["name"] / "weights" / "last.pt"
        if not last.exists():
            print("找不到断点 %s，从头训（去掉 --resume）" % last)
            return 1
        print("从断点续训：%s" % last)
        model = YOLO(str(last))
        model.train(resume=True)
    else:
        model = YOLO(cfg["model"])
        results = model.train(data=str(data), epochs=cfg["epochs"], imgsz=cfg["imgsz"],
                              batch=cfg["batch"], device=a.device,
                              workers=cfg.get("workers", 8),
                              project=a.project, name=cfg["name"], exist_ok=True)

    out = Path(a.project) / cfg["name"]
    metrics = {}
    try:
        m = model.val(data=str(data), device=a.device)
        metrics = {"mAP50": float(m.box.map50), "mAP50_95": float(m.box.map),
                   "precision": float(m.box.mp), "recall": float(m.box.mr)}
    except Exception as e:                       # noqa: BLE001
        print("验证阶段出错：%s" % e)

    (out / "stage_meta.json").write_text(json.dumps({
        "stage": a.stage, "conf_threshold": cfg["conf"], "nms_iou": cfg["iou"],
        "model": cfg["model"], "metrics": metrics,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n权重 %s/weights/best.pt" % out)
    if metrics:
        miss = 1.0 - metrics.get("recall", 0.0)
        print("mAP50 %.3f  召回 %.3f  → 漏检率 %.1f %%（指标 ≤2 %%）%s"
              % (metrics["mAP50"], metrics["recall"], miss * 100,
                 "  达标" if miss <= 0.02 else "  超差，往高召回方向再调"))
    print("\n接进系统：把 configs/system.yaml 的")
    print("  perception.detector 改成 yolo")
    print("  perception.yolo.weights_%s 指向上面的 best.pt" % a.stage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
