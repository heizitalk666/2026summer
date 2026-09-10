#!/usr/bin/env python3
"""把丙的两个 PaDiM 主干 ONNX 转成 RK3576 的 RKNN，并量出 INT8 掉点。

任务书第 2 项点名要 INT8 掉点这个数。这里的口径：
  同一批输入，分别过 ONNX(fp32) 与 RKNN(int8)，比每个位置的特征向量。
  PaDiM 的马氏距离直接建立在这些特征上，所以特征的相对误差就是异常分误差的上界来源。

预处理约定来自 deliverables/丙-异常/artifacts/onnx_smoke.json：
    "bgr-uint8 → float32 0-1 RGB 256×256"
即：cv2 读进来是 BGR → 转 RGB → /255 → NCHW。
RKNN 的 mean_values/std_values 作用在 **RKNN 自己读入的图** 上，
所以配 mean=[0,0,0]、std=[255,255,255]，并让 RKNN 按 RGB 处理。
"""
import json
import os
import sys
import time

import numpy as np

# rknn-toolkit2 2.3.2 调 onnx.mapping，而它在 onnx 1.16 被删了。必须在
# import rknn 之前打上（load_onnx 内部才会拿到）。
import onnx_mapping_shim
_PATCHED = onnx_mapping_shim.install()

HOME = "/home/mingchel_wu/rknn"
ONNX_DIR = os.path.join(HOME, "onnx")
OUT_DIR = os.path.join(HOME, "out")
CALIB_LIST = os.path.join(HOME, "calib_list.txt")
TARGET = "rk3576"

os.makedirs(OUT_DIR, exist_ok=True)

MODELS = [
    ("padim_net2", (1, 128, 32, 32)),
    ("padim_net3", (1, 256, 16, 16)),
]


def load_calib_images(n):
    """读一批图，同时给出两种形态。

    **两边喂的必须是同一张图的不同表示，不能都按自己的习惯预处理一遍。**
    第一版就栽在这：给 RKNN 传了已经 /255 的 float，而 config 里又写着
    ``std_values=[255,255,255]``——RKNN 内部再除一次，等于除了两次 255，
    量出来的"INT8 掉点"余弦只有 0.72、相对误差 77 %。那不是量化误差，
    是输入错了。

    正确的分工：
      RKNN  ← uint8 HWC RGB 原图，由它按 mean/std 自己归一化（与标定集一致）
      ONNX  ← float32 NCHW，手工 /255（onnx_smoke.json 记的约定）
    """
    import cv2
    paths = [p.strip() for p in open(CALIB_LIST) if p.strip()][:n]
    u8s, fps = [], []
    for p in paths:
        img = cv2.imread(p)                     # BGR uint8
        img = cv2.resize(img, (256, 256), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)          # HWC uint8 RGB
        u8s.append(rgb[None])   # (1,256,256,3)，RKNN 要 4 维
        fps.append(np.transpose(rgb.astype(np.float32) / 255.0, (2, 0, 1))[None])
    return paths, u8s, fps


def onnx_ref(onnx_path, xs):
    import onnxruntime as ort
    s = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    iname = s.get_inputs()[0].name
    return [s.run(None, {iname: x})[0] for x in xs]


def convert(name, out_shape, do_quant=True, n_eval=20):
    """转一份 RKNN 并与 ONNX fp32 对账。

    ``do_quant=False`` 是**对照组**：不量化的 RKNN 若也对不上 ONNX，
    那说明误差来自转换/预处理，不是 INT8。只有对照组吻合、量化组掉下去，
    才能把差值算到 INT8 头上——任务书第 2 项要的正是这个数。
    """
    from rknn.api import RKNN

    tag = "int8" if do_quant else "fp"
    onnx_path = os.path.join(ONNX_DIR, name + ".onnx")
    rknn_path = os.path.join(OUT_DIR, "%s_%s.rknn" % (name, tag))
    rep = {"model": name, "target": TARGET, "quant": tag}

    print("\n" + "=" * 62)
    print("转换 %s  →  %s  [%s]" % (name, TARGET, tag))
    print("=" * 62)

    rknn = RKNN(verbose=False)
    # mean=0 / std=255 复刻 onnx_smoke.json 记的 "/255"；输入按 RGB
    rknn.config(mean_values=[[0, 0, 0]], std_values=[[255, 255, 255]],
                target_platform=TARGET, quant_img_RGB2BGR=False)

    if rknn.load_onnx(model=onnx_path) != 0:
        rep["error"] = "load_onnx failed"
        return rep

    t0 = time.time()
    if rknn.build(do_quantization=do_quant,
                  dataset=CALIB_LIST if do_quant else None) != 0:
        rep["error"] = "build(%s) failed" % tag
        return rep
    rep["build_s"] = round(time.time() - t0, 1)

    if rknn.export_rknn(rknn_path) != 0:
        rep["error"] = "export failed"
        return rep
    rep["rknn_bytes"] = os.path.getsize(rknn_path)
    rep["onnx_bytes"] = os.path.getsize(onnx_path)
    rep["shrink_x"] = round(rep["onnx_bytes"] / rep["rknn_bytes"], 2)
    print("  导出 %s  %.2f MB (ONNX %.2f MB, 缩小 %.2fx)"
          % (rknn_path, rep["rknn_bytes"] / 1e6,
             rep["onnx_bytes"] / 1e6, rep["shrink_x"]))

    # ---- INT8 掉点：simulator 上跑，和 ONNX fp32 逐元素比 ----
    if rknn.init_runtime(target=None) != 0:
        rep["error"] = "init_runtime(simulator) failed"
        return rep

    paths, u8s, fps = load_calib_images(n_eval)
    ref = onnx_ref(onnx_path, fps)

    cos, rel, mx = [], [], []
    for x, r in zip(u8s, ref):
        # 传 uint8 HWC，让 RKNN 按 config 的 mean/std 自己归一化——与标定集同路径
        out = rknn.inference(inputs=[x], data_format="nhwc")[0]
        a = np.asarray(out, dtype=np.float64).ravel()
        b = np.asarray(r, dtype=np.float64).ravel()
        if a.shape != b.shape:
            rep["error"] = "shape mismatch rknn=%s onnx=%s" % (out.shape, r.shape)
            rknn.release()
            return rep
        denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1e-12
        cos.append(float(np.dot(a, b) / denom))
        scale = float(np.abs(b).mean()) or 1e-12
        rel.append(float(np.abs(a - b).mean() / scale))
        mx.append(float(np.abs(a - b).max()))

    rep["n_eval"] = len(u8s)
    rep["out_shape"] = list(np.asarray(ref[0]).shape)
    rep["cosine_mean"] = round(float(np.mean(cos)), 6)
    rep["cosine_min"] = round(float(np.min(cos)), 6)
    rep["rel_l1_mean"] = round(float(np.mean(rel)), 6)
    rep["abs_max"] = round(float(np.max(mx)), 6)
    print("  余弦相似度 均值 %.6f  最小 %.6f" % (rep["cosine_mean"], rep["cosine_min"]))
    print("  相对 L1 误差 均值 %.4f %%" % (rep["rel_l1_mean"] * 100))
    rknn.release()
    return rep


if __name__ == "__main__":
    print("onnx.mapping 垫片: %s" % ("已打" if _PATCHED else "无需(onnx 自带)"))
    reports = []
    for name, shape in MODELS:
        for q in (False, True):          # 先跑不量化对照组，再跑 INT8
            try:
                reports.append(convert(name, shape, do_quant=q))
            except Exception as e:                        # noqa: BLE001
                reports.append({"model": name, "quant": "int8" if q else "fp",
                                "error": "%s: %s" % (type(e).__name__, e)})
    out = os.path.join(HOME, "rknn_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)
    print("\n报告已写出 -> %s" % out)
