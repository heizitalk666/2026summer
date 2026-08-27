#!/usr/bin/env python3
"""导出 RKNN，上 RK3576 的 NPU。

    python -m training.export_rknn --weights training/runs/cruise/weights/best.pt

**这一步要等板子到位**，但脚本先写好，理由和串口协议一样：流程定下来，硬件
到货当天就能跑，不用现学 toolkit。

两段式，中间那步是关键：

    best.pt  --ultralytics-->  best.onnx  --rknn-toolkit2-->  best.rknn

**先在 PC 上用 FP32 的 ONNX 验一遍精度，再看 INT8 量化掉了多少。**跳过这步
的话，板上精度不对时分不清是量化掉的还是训练本来就差。脚本因此默认两个数
都报，并把它们写进导出报告。

量化校准集**必须来自本场景**。用 COCO 之类的通用图做校准，量化出来的定标
范围与配电室的低照度、高对比表盘对不上，INT8 掉点会明显大于预期。最省事的
来源是跑过的证据包——`--calib-from-evidence` 直接从 `evidence/` 里取
`cruise_raw.jpg`，那正是巡航态真实分布的样本。

`DetectionEvent.model.quant` 字段就是给这件事留的可追溯性：答辩时要能说清
"这条结论是哪个模型、什么量化跑出来的"。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: RK3576 的 NPU 目标平台标识，rknn-toolkit2 用它选算子实现
TARGET = "rk3576"


def build_calibration_set(evidence_dir: Path, out: Path, limit: int = 200) -> Path:
    """从证据包里攒一份量化校准集。

    取 cruise_raw.jpg（无标注原图）而不是 cruise.jpg：带框的图上多了几条
    彩色线段，会污染定标分布。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    for p in sorted(evidence_dir.glob("*/*/cruise_raw.jpg")):
        paths.append(str(p.resolve()))
        if len(paths) >= limit:
            break
    if not paths:
        raise SystemExit("在 %s 下没找到 cruise_raw.jpg，先跑一轮 run_all 攒样本"
                         % evidence_dir)
    out.write_text("\n".join(paths) + "\n", encoding="utf-8")
    print("校准集 %d 张 → %s" % (len(paths), out))
    return out


def to_onnx(weights: Path, imgsz: int, opset: int) -> Path:
    try:
        from ultralytics import YOLO            # noqa: PLC0415
    except ImportError:
        raise SystemExit("需要 ultralytics：pip install -r requirements-yolo.txt")
    model = YOLO(str(weights))
    # opset 12：rknn-toolkit2 对更高版本的算子支持仍有缺口，遇到不支持的
    # 算子会静默回落到 CPU，NPU 加速就白搭了
    path = model.export(format="onnx", imgsz=imgsz, opset=opset, simplify=True)
    print("ONNX → %s" % path)
    return Path(path)


def eval_fp32(weights: Path, data_yaml: Path | None) -> dict:
    """PC 上跑一遍 FP32 精度，作为量化掉点的基准。"""
    if data_yaml is None or not data_yaml.exists():
        print("未给 --data，跳过 FP32 基准（那样就分不清掉点是量化还是训练问题）")
        return {}
    from ultralytics import YOLO                # noqa: PLC0415
    m = YOLO(str(weights)).val(data=str(data_yaml))
    return {"mAP50": float(m.box.map50), "mAP50_95": float(m.box.map),
            "recall": float(m.box.mr), "precision": float(m.box.mp)}


def to_rknn(onnx: Path, calib_txt: Path, out: Path, *, quant: bool) -> Path:
    try:
        from rknn.api import RKNN               # noqa: PLC0415
    except ImportError:
        raise SystemExit(
            "需要 rknn-toolkit2（只在 x86 Linux 上可装，且与板端 runtime 版本必须一致）：\n"
            "    pip install rknn-toolkit2\n"
            "板子没到时可以先跑到 ONNX 那一步：--stop-at onnx")
    rknn = RKNN(verbose=False)
    # mean/std 与训练时的预处理必须一致，写错了精度会莫名其妙地掉一大截
    rknn.config(mean_values=[[0, 0, 0]], std_values=[[255, 255, 255]],
                target_platform=TARGET, quantized_dtype="asymmetric_quantized-8")
    if rknn.load_onnx(model=str(onnx)) != 0:
        raise SystemExit("load_onnx 失败")
    if rknn.build(do_quantization=bool(quant),
                  dataset=str(calib_txt) if quant else None) != 0:
        raise SystemExit("build 失败")
    out.parent.mkdir(parents=True, exist_ok=True)
    if rknn.export_rknn(str(out)) != 0:
        raise SystemExit("export_rknn 失败")
    rknn.release()
    print("RKNN → %s" % out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="导出 RKNN（RK3576 NPU）")
    ap.add_argument("--weights", required=True)
    ap.add_argument("--data", default=None, help="用于 FP32 基准评测的 data.yaml")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--opset", type=int, default=12)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-quant", action="store_true",
                    help="导 FP16，不做 INT8 量化（先看精度上限）")
    ap.add_argument("--calib-from-evidence", default="evidence",
                    help="从证据包目录攒量化校准集")
    ap.add_argument("--calib-txt", default="training/runs/calib.txt")
    ap.add_argument("--stop-at", choices=["onnx", "rknn"], default="rknn")
    a = ap.parse_args()

    weights = Path(a.weights)
    if not weights.exists():
        raise SystemExit("权重不存在：%s（先跑 python -m training.train_detector）" % weights)

    report = {"weights": str(weights), "target": TARGET,
              "quant": "FP16" if a.no_quant else "INT8"}
    report["fp32"] = eval_fp32(weights, Path(a.data) if a.data else None)

    onnx = to_onnx(weights, a.imgsz, a.opset)
    report["onnx"] = str(onnx)
    if a.stop_at == "onnx":
        print("按 --stop-at onnx 停在这里")
    else:
        calib = build_calibration_set(Path(a.calib_from_evidence),
                                      Path(a.calib_txt)) if not a.no_quant else Path(a.calib_txt)
        out = Path(a.out or weights.with_suffix(".rknn"))
        report["rknn"] = str(to_rknn(onnx, calib, out, quant=not a.no_quant))

    rp = Path("training/runs/export_report.json")
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n导出报告 → %s" % rp)
    print("接进系统：configs/system.yaml → perception.detector: rknn，"
          "并把 model.quant 填成 %s（报文里要可追溯）" % report["quant"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
