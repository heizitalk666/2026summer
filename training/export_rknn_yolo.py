#!/usr/bin/env python3
"""把两级 YOLO11 检测器（cruise_ft / verify_ft）的 ONNX 转成 RK3576 的 RKNN，并在模拟器上量掉点。

在装了 rknn-toolkit2 2.3.2 的 x86 Linux（本项目用 WSL2）上跑：

    ~/rknn/.venv/bin/python training/export_rknn_yolo.py --repo /mnt/c/.../2026summer-main --work ~/rknn/yolo

做三件事：

1. 标定集：从证据包里取巡航与复核原图，外加真实表盘照片，按运行时同样的 letterbox
   补成 1280×1280 存盘。RKNN 读标定图时自己转 RGB、按 mean=0 / std=255 归一化，
   与板上推理的输入约定一致（见 patrol/perception/detector/exported_yolo.py）。
2. 每个模型各出 INT8 与 FP16 两版 .rknn。INT8 用**切头**模型：在检测头解码之前截断成
   6 个输出（每个尺度的 DFL 框 logits 与类别 logits），切出来的 ONNX 另存为
   artifacts/<模型>/best_split.onnx。整图的输出把 0–1280 的坐标和 0–1 的分数拼在一起，
   INT8 一个缩放系数下分数全变成 0，实测一个框都出不来。FP16 用整图。
3. 模拟器评测：留出帧上分别跑 ONNX(FP32) 与 RKNN，用运行时同一份 decode 出框，
   以 FP32 的框为参照算匹配率（同类、IoU ≥ 0.5）、置信度差与框 IoU。

INT8 相对 FP32 召回 ≥ 0.99 且置信度平均漂移 ≤ 0.05 才默认用 INT8，否则默认 FP16（RK3576 NPU 支持 FP16）。
置信度也要管：复核触发规则按 0.25–0.60 的置信度带判，漂移大了同一个目标触不触发复核会变。
结果写 deliverables/甲-检测/rknn/rknn_report.json。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np


def _setup(repo: Path):
    sys.path.insert(0, str(repo))
    # rknn-toolkit2 2.3.2 调 onnx.mapping（onnx 1.16 已删），必须在 import rknn 之前补上
    sys.path.insert(0, str(repo / "deliverables" / "丙-异常" / "rknn"))
    import onnx_mapping_shim
    onnx_mapping_shim.install()


def pick_frames(repo: Path, n_calib: int, n_eval: int, n_real: int):
    ev = sorted(repo.glob("evidence/2026*/*/cruise_raw.jpg")) + sorted(repo.glob("evidence/2026*/*/verify_0[1-3].jpg"))
    real = sorted(repo.glob("training/datasets/seg_combined/images/*/paddlex_*.jpg"))
    # 等距抽样，标定与评测不重叠
    step = max(1, len(ev) // (n_calib + n_eval))
    pool = ev[::step]
    calib = pool[:n_calib] + real[::max(1, len(real) // max(1, n_real))][:n_real]
    evald = pool[n_calib:n_calib + n_eval]
    return calib, evald


def split_head(onnx_path: Path, out_path: Path) -> Path:
    """在 Detect 头的 6 个 Reshape 之前截断：每个尺度一个框分支、一个类别分支。"""
    import onnx
    from onnx import shape_inference
    m = onnx.load(str(onnx_path))
    heads = [n for n in m.graph.node if n.op_type == "Reshape" and n.name.startswith("/model.23/Reshape")]
    heads.sort(key=lambda n: (int(n.name.rsplit("_", 1)[1]) if "_" in n.name.rsplit("/", 1)[1] else 0))
    names = [n.input[0] for n in heads]
    if len(names) != 6:
        raise RuntimeError("%s 的检测头结构与预期不符：找到 %d 个 Reshape" % (onnx_path, len(names)))
    inferred = shape_inference.infer_shapes(m)
    shapes = {vi.name: [d.dim_value for d in vi.type.tensor_type.shape.dim] for vi in inferred.graph.value_info}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    onnx.utils.extract_model(str(onnx_path), str(out_path), ["images"], names)
    print("切头 %s → %s  %s" % (onnx_path.name, out_path, [shapes.get(n) for n in names]), flush=True)
    return out_path


def write_calib(frames, work: Path, size: int) -> Path:
    import cv2
    from patrol.common.imio import imread
    from patrol.perception.detector.exported_yolo import letterbox
    d = work / "calib"
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, f in enumerate(frames):
        img = imread(f)
        if img is None:
            continue
        rgb, _, _ = letterbox(img, size)
        p = d / ("%04d.png" % i)
        cv2.imwrite(str(p), rgb[:, :, ::-1])          # 存 BGR，RKNN 读入时转回 RGB
        lines.append(str(p))
    lst = work / "calib.txt"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst


def convert(onnx: Path, out: Path, dtype: str, calib: Path, size: int, target: str):
    from rknn.api import RKNN
    r = RKNN(verbose=False)
    quant = dtype.startswith("int8")
    algo = "mmse" if dtype == "int8mmse" else "normal"
    r.config(mean_values=[[0, 0, 0]], std_values=[[255, 255, 255]], target_platform=target,
             quant_img_RGB2BGR=False, quantized_algorithm=algo)
    if r.load_onnx(model=str(onnx), inputs=["images"], input_size_list=[[1, 3, size, size]]) != 0:
        raise RuntimeError("load_onnx 失败：%s" % onnx)
    t0 = time.time()
    if r.build(do_quantization=quant, dataset=str(calib) if quant else None) != 0:
        raise RuntimeError("build 失败：%s %s" % (onnx, dtype))
    build_s = time.time() - t0
    if r.export_rknn(str(out)) != 0:
        raise RuntimeError("export_rknn 失败：%s" % out)
    if r.init_runtime() != 0:                          # 不给 target 就是 x86 模拟器
        raise RuntimeError("模拟器 init_runtime 失败")
    return r, build_s


def match(ref, got, iou_thr=0.5):
    """ref/got: [(cls, conf, box)]。返回 (匹配数, 置信度差列表, IoU 列表)。"""
    used, dconf, ious = set(), [], []
    for c, s, b in ref:
        best, bi = 0.0, -1
        for j, (c2, s2, b2) in enumerate(got):
            if j in used or c2 != c:
                continue
            ix = max(0.0, min(b[2], b2[2]) - max(b[0], b2[0]))
            iy = max(0.0, min(b[3], b2[3]) - max(b[1], b2[1]))
            inter = ix * iy
            u = (b[2] - b[0]) * (b[3] - b[1]) + (b2[2] - b2[0]) * (b2[3] - b2[1]) - inter
            iou = inter / u if u > 0 else 0.0
            if iou > best:
                best, bi = iou, j
        if bi >= 0 and best >= iou_thr:
            used.add(bi)
            dconf.append(abs(s - got[bi][1]))
            ious.append(best)
    return len(used), dconf, ious


def evaluate(rknn, onnx: Path, frames, size: int, conf: float, nms_iou: float):
    import onnxruntime as ort
    from patrol.common.imio import imread
    from patrol.perception.detector.exported_yolo import decode, letterbox, split_to_raw
    sess = ort.InferenceSession(str(onnx), providers=["CPUExecutionProvider"])
    n_ref = n_got = n_match = 0
    dconf, ious, t_sim = [], [], []
    for f in frames:
        img = imread(f)
        rgb, s, pad = letterbox(img, size)
        x = rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        ref = decode(sess.run(None, {"images": x})[0], conf_threshold=conf, iou_threshold=nms_iou,
                     scale=s, pad=pad, image_wh=(img.shape[1], img.shape[0]))
        t0 = time.time()
        outs = rknn.inference(inputs=[rgb[None]], data_format=["nhwc"])
        raw = outs[0] if len(outs) == 1 else split_to_raw(outs, size)
        t_sim.append(time.time() - t0)
        got = decode(raw, conf_threshold=conf, iou_threshold=nms_iou, scale=s, pad=pad,
                     image_wh=(img.shape[1], img.shape[0]))
        m, dc, io = match(ref, got)
        n_ref += len(ref)
        n_got += len(got)
        n_match += m
        dconf += dc
        ious += io
    return {
        "frames": len(frames), "fp32_boxes": n_ref, "rknn_boxes": n_got, "matched": n_match,
        "recall_vs_fp32": round(n_match / n_ref, 4) if n_ref else None,
        "precision_vs_fp32": round(n_match / n_got, 4) if n_got else None,
        "conf_abs_diff_mean": round(float(np.mean(dconf)), 4) if dconf else None,
        "conf_abs_diff_max": round(float(np.max(dconf)), 4) if dconf else None,
        "box_iou_mean": round(float(np.mean(ious)), 4) if ious else None,
        "simulator_s_per_frame": round(float(np.mean(t_sim)), 2),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="YOLO11 ONNX → RK3576 RKNN（INT8 / FP16）并量掉点")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--work", default=os.path.expanduser("~/rknn/yolo"))
    ap.add_argument("--models", nargs="+", default=["cruise_ft", "verify_ft"])
    ap.add_argument("--dtypes", nargs="+", default=["int8", "fp16"],
                    help="int8（normal 量化）/ int8mmse（mmse 量化，1280 输入上每个模型要约 2 小时，默认不跑）/ fp16")
    ap.add_argument("--n-calib", type=int, default=170)
    ap.add_argument("--n-real", type=int, default=30)
    ap.add_argument("--n-eval", type=int, default=40)
    ap.add_argument("--size", type=int, default=1280)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--nms-iou", type=float, default=0.45)
    ap.add_argument("--target", default="rk3576")
    a = ap.parse_args(argv)

    repo, work = Path(a.repo), Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    _setup(repo)
    calib_frames, eval_frames = pick_frames(repo, a.n_calib, a.n_eval, a.n_real)
    calib = write_calib(calib_frames, work, a.size)
    print("标定集 %d 张，评测集 %d 帧" % (len(calib_frames), len(eval_frames)), flush=True)

    results = []
    for name in a.models:
        onnx = repo / "artifacts" / name / "best.onnx"
        for dtype in a.dtypes:
            out = work / "out" / ("%s_%s.rknn" % (name, dtype))
            out.parent.mkdir(parents=True, exist_ok=True)
            print("转换 %s %s ..." % (name, dtype), flush=True)
            src = onnx
            if dtype.startswith("int8"):
                src = split_head(onnx, repo / "artifacts" / name / "best_split.onnx")
            r, build_s = convert(src, out, dtype, calib, a.size, a.target)
            ev = evaluate(r, onnx, eval_frames, a.size, a.conf, a.nms_iou)
            r.release()
            row = {"model": name, "dtype": dtype, "graph": "split" if src != onnx else "full",
                   "rknn_bytes": out.stat().st_size,
                   "onnx_bytes": onnx.stat().st_size, "build_s": round(build_s, 1), **ev}
            print(json.dumps(row, ensure_ascii=False), flush=True)
            results.append(row)

    out = repo / "deliverables" / "甲-检测" / "rknn" / "rknn_report.json"
    # 与上一次的报告合并：同一个 (模型, 精度) 以本次为准，其余保留
    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8")).get("results", [])
            mine = {(r["model"], r["dtype"]) for r in results}
            results = [r for r in old if (r.get("model"), r.get("dtype")) not in mine and r.get("frames", 0) >= a.n_eval] + results
        except (OSError, ValueError, KeyError):
            pass
    models = sorted({r["model"] for r in results})
    choice = {}
    for name in models:
        rows = {r["dtype"]: r for r in results if r["model"] == name}
        ok = [d for d in ("int8", "int8mmse") if d in rows and (rows[d]["recall_vs_fp32"] or 0) >= 0.99
              and rows[d]["conf_abs_diff_mean"] is not None and rows[d]["conf_abs_diff_mean"] <= 0.05]
        # INT8 候选里挑置信度漂移最小的；都不达标就用 FP16
        choice[name] = min(ok, key=lambda d: rows[d]["conf_abs_diff_mean"]) if ok else "fp16"
    report = {
        "target": a.target, "toolkit": "rknn-toolkit2 2.3.2", "date": time.strftime("%Y-%m-%d"),
        "input": "uint8 RGB NHWC 1280×1280，letterbox 补灰 114；模型内 mean=0 / std=255",
        "calibration": {"images": len(calib_frames), "virtual_frames": len(calib_frames) - a.n_real,
                        "real_gauge_photos": a.n_real,
                        "source": "evidence/*/cruise_raw.jpg 与 verify_0[1-3].jpg 等距抽样 + seg_combined 的 PaddleX 真实表盘照片"},
        "evaluation": {"frames": len(eval_frames), "reference": "同一份 ONNX 在 onnxruntime(FP32) 上的输出",
                       "match": "同类别 IoU ≥ 0.5", "conf_threshold": a.conf, "nms_iou": a.nms_iou,
                       "note": "x86 模拟器上的数值对照，不是板上速度"},
        "results": results,
        "default_choice": choice,
        "rule": "INT8 相对 FP32 召回 ≥ 0.99 且置信度平均漂移 ≤ 0.05 才用 INT8（复核触发按 0.25–0.60 置信度带判，漂移大了会改变触发），否则用 FP16",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("报告写到 %s；默认选择 %s" % (out, choice), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
