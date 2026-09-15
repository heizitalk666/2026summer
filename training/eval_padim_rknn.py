#!/usr/bin/env python3
"""L3 PaDiM 的两个特征网络转 RK3576 RKNN，在模拟器上跑完整 L3 评测集，量到误报 / 漏报。

deliverables/丙-异常/rknn/convert_rknn.py 量的是特征层面的掉点（相对 L1、余弦）。这里再往前
走一步，量到判定：同一份评测集（out/l3_eval，106 正常 / 120 异常，由 training/bench_anomaly.py
生成），分别用 ONNX(FP32) 与 RKNN（INT8、FP）出特征，打分走板上用的同一份
PadimNumpyAnomaly，阈值 0.55，统计误报、漏报，以及相对 ONNX 翻转了几个判定。

在装了 rknn-toolkit2 2.3.2 的 x86 Linux（本项目用 WSL2）上跑：

    ~/rknn/.venv/bin/python training/eval_padim_rknn.py --repo /mnt/c/.../2026summer-main \\
        --calib-list ~/rknn/calib_list.txt --work ~/rknn/padim

转换参数与 convert_rknn.py 相同：mean=0 / std=255、按 RGB 输入，INT8 用同一份 100 张正常裁片标定。
转出的 .rknn 写到 <work>/out/，打部署包从这里取（deploy/build_package.py --padim-rknn-dir），
评测的就是装上车的那一份。

INT8 的误报数、漏报数都不比 ONNX 多出 1 个以上，且翻转的判定不超过 2 %，才默认用 INT8，否则用 FP。
结果写 deliverables/丙-异常/rknn/padim_bench_rknn.json。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

THR = 0.55


def _setup(repo: Path) -> None:
    sys.path.insert(0, str(repo))
    # rknn-toolkit2 2.3.2 调 onnx.mapping（onnx 1.16 已删），必须在 import rknn 之前补上
    sys.path.insert(0, str(repo / "deliverables" / "丙-异常" / "rknn"))
    import onnx_mapping_shim
    onnx_mapping_shim.install()


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_set(repo: Path):
    import cv2
    items = []
    for label in ("normal", "anomaly"):
        for p in sorted((repo / "out" / "l3_eval" / label).glob("*.jpg")):
            img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                items.append((label, p.name, img))
    if not items:
        raise SystemExit("out/l3_eval 下没有评测图：先跑 python -m training.bench_anomaly")
    return items


def build(onnx_path: Path, out_path: Path, dtype: str, calib_list: Path, target: str):
    from rknn.api import RKNN
    r = RKNN(verbose=False)
    r.config(mean_values=[[0, 0, 0]], std_values=[[255, 255, 255]], target_platform=target,
             quant_img_RGB2BGR=False)
    quant = dtype == "int8"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    steps = (("load_onnx", lambda: r.load_onnx(model=str(onnx_path))),
             ("build", lambda: r.build(do_quantization=quant, dataset=str(calib_list) if quant else None)),
             ("export_rknn", lambda: r.export_rknn(str(out_path))),
             ("init_runtime", lambda: r.init_runtime(target=None)))
    for name, fn in steps:
        if fn() != 0:
            raise RuntimeError("%s 失败：%s [%s]" % (name, onnx_path.name, dtype))
    return r


def score_all(det, items) -> list[float]:
    # 与 bench_anomaly.score_dir 同口径：整张裁片作为检测框
    return [float(det.score(img, (0.0, 0.0, float(img.shape[1]), float(img.shape[0]))).anomaly_score)
            for _, _, img in items]


def summarize(scores, labels, ref=None) -> dict:
    s, lab = np.asarray(scores), np.asarray(labels)
    norm, anom = s[lab == "normal"], s[lab == "anomaly"]
    fp, fn = int((norm > THR).sum()), int((anom <= THR).sum())
    row = {"n_normal": int(norm.size), "n_anomaly": int(anom.size), "fp": fp, "fn": fn,
           "fpr": round(fp / norm.size, 4), "fnr": round(fn / anom.size, 4),
           "mean_score_normal": round(float(norm.mean()), 4), "mean_score_anomaly": round(float(anom.mean()), 4)}
    if ref is not None:
        r = np.asarray(ref)
        d = np.abs(s - r)
        row.update({"score_abs_diff_median": round(float(np.median(d)), 4),
                    "score_abs_diff_p95": round(float(np.percentile(d, 95)), 4),
                    "score_abs_diff_max": round(float(d.max()), 4),
                    "flips_vs_onnx": int(((s > THR) != (r > THR)).sum())})
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PaDiM 特征网络 RKNN（INT8 / FP）在 L3 评测集上的误报漏报")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--calib-list", required=True, help="INT8 标定图列表（与 convert_rknn.py 同一份）")
    ap.add_argument("--work", default=os.path.expanduser("~/rknn/padim"))
    ap.add_argument("--dtypes", nargs="+", default=["int8", "fp"])
    ap.add_argument("--target", default="rk3576")
    a = ap.parse_args(argv)

    repo, work = Path(a.repo), Path(a.work)
    _setup(repo)
    from patrol.perception.anomaly import PadimNumpyAnomaly

    runs = repo / "training" / "runs" / "anomaly"
    stats = runs / "padim_cov_stats.npz"
    nets = {k: runs / ("padim_net%d.onnx" % k) for k in (2, 3)}
    items = load_set(repo)
    labels = [lab for lab, _, _ in items]
    print("评测集 %d 张（正常 %d / 异常 %d）" % (len(items), labels.count("normal"), labels.count("anomaly")), flush=True)

    ref_det = PadimNumpyAnomaly(str(stats), str(nets[2]), str(nets[3]), backend="onnx", threshold=THR)
    t0 = time.time()
    ref = score_all(ref_det, items)
    rows = [{"backend": "onnxruntime", "dtype": "fp32", **summarize(ref, labels),
             "s_per_roi": round((time.time() - t0) / len(items), 4)}]
    print(json.dumps(rows[-1], ensure_ascii=False), flush=True)

    for dtype in a.dtypes:
        rk, files = [], {}
        for k in (2, 3):
            out = work / "out" / ("padim_net%d_%s.rknn" % (k, dtype))
            rk.append(build(nets[k], out, dtype, Path(a.calib_list), a.target))
            files["padim_net%d" % k] = {"bytes": out.stat().st_size, "sha256": _sha256(out)}
        det = PadimNumpyAnomaly(str(stats), str(nets[2]), str(nets[3]), backend="onnx", threshold=THR)
        # 只换特征网络：_run 按 backend 走 RKNN 的 inference 调用，打分代码与板上完全相同
        det.backend, det.model_name, det._nets = "rknn", "padim_cov_np_rknn_sim_%s" % dtype, rk
        t0 = time.time()
        sc = score_all(det, items)
        rows.append({"backend": "rknn-simulator", "dtype": dtype, **summarize(sc, labels, ref),
                     "s_per_roi": round((time.time() - t0) / len(items), 4), "files": files})
        for r in rk:
            r.release()
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)

    onnx_row = rows[0]
    i8 = next((r for r in rows if r["dtype"] == "int8"), None)
    choice = "fp"
    if i8 and i8["fp"] <= onnx_row["fp"] + 1 and i8["fn"] <= onnx_row["fn"] + 1 \
            and i8["flips_vs_onnx"] <= 0.02 * len(items):
        choice = "int8"

    torch_ref = None
    cmp_path = repo / "out" / "l3_eval" / "comparison.json"
    if cmp_path.exists():
        pc = json.loads(cmp_path.read_text(encoding="utf-8")).get("padim_cov") or {}
        torch_ref = {k: pc.get(k) for k in ("model", "fp", "fn", "fpr", "fnr")}

    report = {
        "target": a.target, "toolkit": "rknn-toolkit2 2.3.2", "date": time.strftime("%Y-%m-%d"),
        "threshold": THR,
        "eval_set": "out/l3_eval（training/bench_anomaly.py 生成）：正常 %d / 异常 %d"
                    % (labels.count("normal"), labels.count("anomaly")),
        "scorer": "patrol/perception/anomaly.py::PadimNumpyAnomaly（板上同一份打分代码）",
        "stats": {"path": "training/runs/anomaly/padim_cov_stats.npz", "sha256": _sha256(stats)},
        "onnx": {"padim_net%d" % k: {"path": "training/runs/anomaly/padim_net%d.onnx" % k, "sha256": _sha256(nets[k])}
                 for k in (2, 3)},
        "rknn_config": "mean_values=[0,0,0], std_values=[255,255,255], quant_img_RGB2BGR=False；INT8 标定 %s"
                       % Path(a.calib_list).name,
        "torch_reference": torch_ref,
        "results": rows,
        "default_choice": choice,
        "rule": "INT8 的误报数、漏报数都不比 ONNX 多出 1 个以上，且翻转的判定不超过 2 %，才用 INT8，否则用 FP",
        "note": "x86 模拟器上的数值对照；s_per_roi 是模拟器耗时，不代表板上速度",
    }
    out = repo / "deliverables" / "丙-异常" / "rknn" / "padim_bench_rknn.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("报告写到 %s；默认选择 %s" % (out, choice), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
