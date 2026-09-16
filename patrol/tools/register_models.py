#!/usr/bin/env python3
"""把三人交付里的模型元数据登记进云端台账（任务书第 7 项「模型版本管理」）。

    python -m patrol.tools.register_models --list     # 只看要登记什么，不写
    python -m patrol.tools.register_models            # 直接写 SQLite 台账
    python -m patrol.tools.register_models --url http://127.0.0.1:8000   # 走 HTTP

**为什么需要这个工具。** `/api/models` 接口从一开始就在，但登记数一直是 0——
不是接口没做，是没人去登记，而三个人的交付里其实早就有可登记的元数据
（`stage_meta_cruise.json` 的 mAP、`unet.json` 的 IoU、`l3_report.json` 的
误报漏报）。手工往接口里贴 JSON 谁都不会去做第二次，写成脚本才可复现。

**元数据来源即真值。** 这里不重算任何指标，只从各自交付目录里读，读不到就
如实登记为空并在 note 里写明缺什么。**权重不在库里的，`weights_sha` 留空**
——登记的是"哪一版模型产生了这条结论"，编一个哈希比留空更糟。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DELIV = REPO / "deliverables"


def _cfg_path(key: str, fallback: str) -> Path:
    """按 configs/system.yaml 里写的路径找权重。

    **不要在这里第二次写死路径。**原先这里写的是
    ``training/runs/cruise/best.pt``，而 config 里是
    ``training/runs/cruise/weights/best.pt``——差一层 ``weights/``。
    结果是权重明明放对了位置（config 那条路径），台账里 ``weights_sha``
    照样是空的，看上去像"权重没交"。同一个路径在两个地方各写一遍，
    迟早漂成两个值。
    """
    from patrol.common.config import Config
    try:
        return REPO / str(Config.load().get(key, fallback))
    except Exception:                                    # noqa: BLE001
        return REPO / fallback


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect() -> list[dict]:
    """按 stage 收集要登记的模型。stage 与 ICD 的两级检测口径一致。"""
    out: list[dict] = []

    # ---- 甲 · L1 检测（巡航级基线，只用公开集训练）。已被下面的 cruise_ft 取代，不部署。
    # 配置里的 perception.yolo 指的是 _ft，已经没有指向基线权重的键，只能直接写路径
    meta = _load(DELIV / "甲-检测" / "artifacts" / "stage_meta_cruise.json")
    if meta:
        m = meta.get("metrics", {})
        out.append({
            "version": "yolo11s-cruise-w1",
            "stage": "cruise",
            "weights_sha": _sha256(REPO / "training" / "runs" / "cruise" / "weights" / "best.pt"),
            "dataset": "roboflow/distribution_room",
            "metrics": {k: round(float(v), 4) for k, v in m.items()},
            "note": "甲 · L1 巡航级检测基线（yolo11s，只用公开集训练）。虚拟配电室里检出率为 0，"
                    "已由混合微调的 cruise_ft 取代，不部署。",
            "activate": False,
        })
    # ---- 甲 · L1 检测（复核级）。训完之前这里登记的是占位，训完之后读交付里的
    # stage_meta.json——**别把占位写死**，否则台账会一直说"未训"而交付目录里
    # 明明躺着 80 轮的记录。
    vmeta = _load(DELIV / "甲-检测" / "verify" / "stage_meta.json")
    if vmeta:
        vm = vmeta.get("metrics", {})
        out.append({
            "version": "yolo11m-verify-w1",
            "stage": "verify",
            "weights_sha": _sha256(DELIV / "甲-检测" / "verify" / "weights" / "best.pt"),
            "dataset": "roboflow/distribution_room",
            "metrics": {k: round(float(v), 4) for k, v in vm.items()},
            "note": "甲 · L1 复核级检测基线（%s 起训 80 轮，只用公开集），已由 verify_ft 取代，不部署。"
                    "mAP50 %.4f 低于巡航级基线，「复核比巡航高 5 个点」那条验收标准在当前数据集上没有余量。"
                    % (vmeta.get("model", "?"), float(vm.get("mAP50", 0))),
            "activate": False,
        })
    elif meta:
        out.append({
            "version": "yolo11m-verify-未训",
            "stage": "verify",
            "weights_sha": None,
            "dataset": "roboflow/distribution_room",
            "metrics": {},
            "note": "甲 · L1 复核级检测：**未训练**。登记占位是为了让台账显示"
                    "这一级缺失，而不是看起来只有一级模型。",
            "activate": False,
        })

    # ---- 甲 · L1 检测，系统部署的两级：公开集基线 + 合成渲染 ×3 混合微调 15 轮（configs 的 perception.yolo）
    ft_sha: dict[str, str] = {}
    for stage, run, arch in (("cruise", "cruise_ft", "yolo11s"), ("verify", "verify_ft", "yolo11m")):
        fmeta = _load(DELIV / "甲-检测" / "figures" / run / "stage_meta.json")
        if not fmeta:
            continue
        sha = _sha256(_cfg_path("perception.yolo.weights_%s" % stage, "training/runs/%s/weights/best.pt" % run))
        if sha:
            ft_sha[stage] = sha
        out.append({
            "version": "%s-%s-ft" % (arch, stage),
            "stage": stage,
            "weights_sha": sha,
            "dataset": "roboflow/distribution_room + 合成渲染 ×3",
            "metrics": {k: round(float(v), 4) for k, v in fmeta.get("metrics", {}).items()},
            "note": "甲 · L1 %s级检测，**系统部署的那一版**（%s，公开集基线上混合微调 15 轮）。"
                    "上板转 RKNN FP16，40 帧上与 ONNX 逐框一致，见 deliverables/甲-检测/rknn/rknn_report.json。"
                    % ("巡航" if stage == "cruise" else "复核", arch),
            "activate": True,
        })
    # 基线路径上的文件若与部署版逐字节相同，说明基线权重在这台机器上已被覆盖：哈希留空，不许冒名
    for it in out:
        if not it["activate"] and it["stage"] in ft_sha and it["weights_sha"] == ft_sha[it["stage"]]:
            it["weights_sha"] = None
            it["note"] += "（基线路径上的文件与部署版逐字节相同，基线权重已不在，哈希留空。）"

    # ---- 乙 · L2 分割
    unet = _load(DELIV / "乙-分割" / "artifacts" / "unet.json")
    if unet:
        split = _load(DELIV / "乙-分割" / "artifacts" / "split.json") or {}
        comp = (split.get("val_composition") or {})
        out.append({
            "version": "unet-seg-w1",
            "stage": "segment",
            "weights_sha": _sha256(_cfg_path("perception.segmenter.weights",
                                             "training/runs/seg/unet.onnx")),
            "dataset": "gen_synthetic + paddlex/meter_seg",
            "metrics": {
                "needle_iou": round(float(unet.get("unet_needle_iou", 0)), 4),
                "baseline_needle_iou": round(float(unet.get("baseline_needle_iou", 0)), 4),
                "n_val_roi": unet.get("n_val_roi"),
                "val_roi_paddlex_frac": comp.get("val_roi_paddlex_frac"),
            },
            "note": "乙 · L2 分割（U-Net，ONNX 31.0 MB）。**比选结论是不启用**：合成"
                    "表盘上级联读数 P90 0.36–0.40 %FS，不优于几何法的 0.18–0.19 %FS。"
                    "权重已就位，故这条结论现在可当场复跑验证。"
                    "needle_iou 0.7784 是交付记录的 train 模式 BN 口径；乙补交 val 划分后本机"
                    "逐位复现，修掉 _unet_pred_fn 缺 model.eval() 之后按部署口径复算为 0.8118"
                    "（python -m training.eval_unet，2026-09-10）。",
            "activate": False,
        })

    # ---- 丙 · L3 异常
    l3 = _load(DELIV / "丙-异常" / "artifacts" / "l3_report.json")
    base = _load(DELIV / "丙-异常" / "artifacts" / "baseline.json")
    if l3 or base:
        out.append({
            "version": "statistical-l3-w0",
            "stage": "anomaly",
            "weights_sha": None,
            "dataset": "gen_synthetic/normal_patches",
            "metrics": {"note": "零权重，在线学习 + 马氏距离"},
            "note": "丙 · L3 统计法基线。零权重（故 weights_sha 留空）、当场可跑、"
                    "可解释。**已不再是默认**：perception.l3.model 现为 padim_s，"
                    "统计法退为 torch 缺席 / 权重丢失时的兜底。",
            "activate": False,
        })
        out.append({
            "version": "padim-cov-l3-w1",
            "stage": "anomaly",
            "weights_sha": _sha256(_cfg_path("perception.l3.weights",
                                             "training/runs/anomaly/padim_cov.pt")),
            "dataset": "gen_synthetic --n 300 --seed 7 (259) + "
                       "augment_verify_geometry (537) = 796",
            "metrics": {"false_positive_rate": 0.038, "false_negative_rate": 0.033},
            "note": "丙 · L3 PaDiM 全协方差，**系统当前启用的那一路**"
                    "（configs 的 perception.l3.model 已是 padim_s）。三个脚本"
                    "与 anomaly.py 的 PadimAnomaly 都已入库，训练集可由 "
                    "gen_synthetic + augment_verify_geometry 完整重建"
                    "（实测 259+537=796，与交付记录逐个对上）。"
                    "开发机上走 torch（主干 resnet18 用 torchvision 预训练权重，需本地缓存）；"
                    "上板走 padim_np：统计量导出为 padim_cov_stats.npz，两个特征网络转 RKNN INT8，"
                    "整套 L3 评测集上误报、漏报与 torch 版相同"
                    "（deliverables/丙-异常/rknn/padim_bench_rknn.json）。",
            "activate": True,
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="登记模型版本到云端台账")
    ap.add_argument("--list", action="store_true", help="只打印，不写入")
    ap.add_argument("--url", help="走 HTTP 接口而不是直接写 SQLite")
    ap.add_argument("--db", default=str(REPO / "cloud" / "patrol.db"))
    a = ap.parse_args(argv)

    items = collect()
    if not items:
        print("deliverables/ 下没找到任何模型元数据", file=sys.stderr)
        return 1

    print("%-22s %-9s %-8s %s" % ("version", "stage", "权重", "关键指标"))
    for it in items:
        m = it["metrics"]
        brief = ", ".join("%s=%s" % (k, v) for k, v in list(m.items())[:2]) or "—"
        print("%-22s %-9s %-8s %s"
              % (it["version"], it["stage"],
                 "有" if it["weights_sha"] else "缺", brief))
    if a.list:
        print("\n--list：未写入")
        return 0

    ts = int(time.time() * 1000)
    if a.url:
        import urllib.request
        for it in items:
            body = json.dumps(it, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                a.url.rstrip("/") + "/api/models", data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
        print("\n已通过 %s 登记 %d 条" % (a.url, len(items)))
        return 0

    sys.path.insert(0, str(REPO))
    from cloud.db import Ledger
    led = Ledger(a.db)
    for it in items:
        led.register_model(it["version"], it["stage"],
                           weights_sha=it["weights_sha"], dataset=it["dataset"],
                           metrics=it["metrics"], note=it["note"],
                           ts_utc_ms=ts, activate=it["activate"])
    print("\n已写入 %s，共 %d 条（台账「模型」页可见）" % (a.db, len(items)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
