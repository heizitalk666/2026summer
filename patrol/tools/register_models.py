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

    # ---- 甲 · L1 检测（巡航级）
    meta = _load(DELIV / "甲-检测" / "artifacts" / "stage_meta_cruise.json")
    if meta:
        m = meta.get("metrics", {})
        out.append({
            "version": "yolo11s-cruise-w1",
            "stage": "cruise",
            "weights_sha": _sha256(REPO / "training" / "runs" / "cruise" / "best.pt"),
            "dataset": "roboflow/distribution_room",
            "metrics": {k: round(float(v), 4) for k, v in m.items()},
            "note": "甲 · L1 巡航级检测。权重未入库（19 MB，见 deliverables/甲-检测/"
                    "artifacts/where.txt），故 weights_sha 为空。单帧耗时与漏检率未测。",
            "activate": False,
        })
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

    # ---- 乙 · L2 分割
    unet = _load(DELIV / "乙-分割" / "artifacts" / "unet.json")
    if unet:
        split = _load(DELIV / "乙-分割" / "artifacts" / "split.json") or {}
        comp = (split.get("val_composition") or {})
        out.append({
            "version": "unet-seg-w1",
            "stage": "segment",
            "weights_sha": _sha256(REPO / "training" / "runs" / "seg" / "unet.onnx"),
            "dataset": "gen_synthetic + paddlex/meter_seg",
            "metrics": {
                "needle_iou": round(float(unet.get("unet_needle_iou", 0)), 4),
                "baseline_needle_iou": round(float(unet.get("baseline_needle_iou", 0)), 4),
                "n_val_roi": unet.get("n_val_roi"),
                "val_roi_paddlex_frac": comp.get("val_roi_paddlex_frac"),
            },
            "note": "乙 · L2 分割。**比选结论是不启用**：合成表盘上级联读数 P90 "
                    "0.36–0.40 %FS，不优于几何法的 0.18–0.19 %FS。"
                    "权重 31 MB 未入库，weights_sha 为空。",
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
            "note": "丙 · L3 统计法基线。零权重、当场可跑、可解释（异常分来自哪个"
                    "特征通道说得清），是系统当前实际启用的那一路。",
            "activate": True,
        })
        out.append({
            "version": "padim-cov-l3-w1",
            "stage": "anomaly",
            "weights_sha": None,
            "dataset": "gen_synthetic/normal_patches + augment_verify_geometry",
            "metrics": {"false_positive_rate": 0.038, "false_negative_rate": 0.033},
            "note": "丙 · L3 PaDiM 全协方差。**交付的数字无法复现**：产生它的三个脚本"
                    "（augment_verify_geometry / bench_anomaly / export_padim_onnx）"
                    "与 anomaly.py 里的 PaDiM 实现都不在仓库里，configs 的 "
                    "perception.l3.model 仍是 efficientad_s。登记它是为了让缺口"
                    "出现在台账上，不是为了背书这两个数。",
            "activate": False,
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
