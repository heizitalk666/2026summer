#!/usr/bin/env python3
"""统一导出到 ONNX，并当场跑一遍冒烟推理。

    python -m training.export_onnx --detector training/runs/cruise/weights/best.pt
    python -m training.export_onnx --segmenter my_unet.pt --seg-arch mypkg.UNet

**为什么统一走 ONNX。**训练侧想用什么框架都行，部署侧只认一种格式；而
RK3576 的 RKNN 工具链吃的正是 ONNX——同一份权重，在电脑上用 onnxruntime
跑，在板子上转 RKNN 跑，patrol/ 下一行代码都不用改。

**为什么导出完必须当场推一遍。**"导出成功"和"能用"是两件事：形状对不上、
输出通道顺序反了、动态维度没设对，这些在导出时全都不报错，要等到车上跑
起来、掩膜整个错位、读数莫名其妙偏几度，才有人回头查。那时排查成本是现在
的几十倍。所以这里导完就按 patrol/perception/segment/onnx_seg.py 约定的
形状喂一张假图进去，形状不对就当场失败。

约定（onnx_seg.py 里也写了一份，两处必须一致）：

    分割   输入 (1, 3, H, W) float32 BGR/255；输出 (1, 4, H, W) logits
    检测   由 ultralytics 自己的导出器负责，这里只做冒烟推理
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def smoke(path: Path, *, want_out_ch: int | None = None,
          size: int = 256) -> bool:
    """喂一张假图进去，检查输出形状。**这一步不通过就别提交权重。**"""
    try:
        import onnxruntime as ort
    except ImportError:
        print("  跳过冒烟推理：没装 onnxruntime（pip install onnxruntime）")
        return True
    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    shape = [d if isinstance(d, int) else (1 if i == 0 else size)
             for i, d in enumerate(inp.shape)]
    x = np.zeros(shape, np.float32)
    out = sess.run(None, {inp.name: x})[0]
    print("  输入 %s %s → 输出 %s" % (inp.name, shape, list(out.shape)))
    if want_out_ch is not None:
        if len(out.shape) != 4 or out.shape[1] != want_out_ch:
            print("  ✗ 输出应当是 (1, %d, H, W)，实际 %s。"
                  "通道数不对时下游会静默给出错位的掩膜——现在失败比上车再查便宜得多"
                  % (want_out_ch, list(out.shape)))
            return False
    print("  ✓ 形状符合约定")
    return True


def export_detector(weights: Path, out: Path, imgsz: int) -> bool:
    try:
        from ultralytics import YOLO
    except ImportError:
        print("没装 ultralytics，跳过检测器导出（pip install ultralytics）")
        return False
    m = YOLO(str(weights))
    p = Path(m.export(format="onnx", imgsz=imgsz, opset=12, simplify=False))
    out.parent.mkdir(parents=True, exist_ok=True)
    if p.resolve() != out.resolve():
        p.replace(out)
    print("检测器导出到 %s" % out)
    return smoke(out, size=imgsz)


def export_segmenter(weights: Path, out: Path, arch: str, size: int) -> bool:
    """把你们训的分割网络导出。arch 形如 `mypkg.models.UNet`。"""
    try:
        import importlib
        import torch
    except ImportError:
        print("没装 torch，跳过分割导出")
        return False
    mod, _, cls = arch.rpartition(".")
    Net = getattr(importlib.import_module(mod), cls)
    net = Net()
    net.load_state_dict(torch.load(weights, map_location="cpu"))
    net.eval()
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(net, torch.zeros(1, 3, size, size), str(out),
                      opset_version=12, input_names=["images"],
                      output_names=["logits"],
                      dynamic_axes={"images": {2: "h", 3: "w"},
                                    "logits": {2: "h", 3: "w"}})
    print("分割导出到 %s" % out)
    from patrol.perception.segment.pixel import N_CLASS
    return smoke(out, want_out_ch=N_CLASS, size=size)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="导出 ONNX 并冒烟验证")
    ap.add_argument("--detector", default=None, help="ultralytics .pt 权重")
    ap.add_argument("--segmenter", default=None, help="分割 .pt 权重")
    ap.add_argument("--seg-arch", default=None,
                    help="分割网络的类路径，如 mypkg.models.UNet")
    ap.add_argument("--check", default=None, help="只对已有的 .onnx 做冒烟推理")
    ap.add_argument("--out-dir", default="training/runs/onnx")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--seg-size", type=int, default=256)
    a = ap.parse_args(argv)

    outd = Path(a.out_dir)
    ok = True
    if a.check:
        from patrol.perception.segment.pixel import N_CLASS
        print("冒烟推理 %s" % a.check)
        ok &= smoke(Path(a.check),
                    want_out_ch=N_CLASS if "seg" in a.check else None,
                    size=a.seg_size)
    if a.detector:
        ok &= export_detector(Path(a.detector), outd / "detector.onnx", a.imgsz)
    if a.segmenter:
        if not a.seg_arch:
            raise SystemExit("--segmenter 需要同时给 --seg-arch")
        ok &= export_segmenter(Path(a.segmenter), outd / "segmenter.onnx",
                               a.seg_arch, a.seg_size)
    if not (a.detector or a.segmenter or a.check):
        ap.print_help()
        return 0
    if ok:
        print("\n全部通过。启用方式：configs 里把对应的 backend 改成 onnx、"
              "weights 指到导出的文件")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
