#!/usr/bin/env python3
"""一键起全系统。

    python -m patrol.tools.run_all                 # 四进程 + 云端，跑到 Ctrl-C
    python -m patrol.tools.run_all --seconds 90    # 跑 90 秒后自动收工并出报告
    python -m patrol.tools.run_all --no-cloud      # 只跑边缘端

进程与 ICD §1.1 的划分一一对应：

    gateway     安全网关，唯一能碰执行器的进程，先起
    perception  感知，10 Hz 发 IF-1
    mission     复核状态机，发 IF-2、5 Hz 心跳
    uploader    证据包落盘与上传
    cloud       台账服务（可选）

**起停顺序有讲究**：网关先起（它绑定 REP 与 PUB 端口），最后停；mission
先停（停了心跳，网关的看门狗会介入让车走完路线，这正是设计要的行为）。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

NODES = [
    ("gateway", [sys.executable, "-m", "patrol.gateway.node"]),
    ("perception", [sys.executable, "-m", "patrol.perception.node"]),
    ("mission", [sys.executable, "-m", "patrol.mission.node"]),
    ("uploader", [sys.executable, "-m", "patrol.uploader.node"]),
]


def wait_http(url: str, timeout_s: float = 15.0) -> bool:
    import requests
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            if requests.get(url, timeout=1.0).status_code < 400:
                return True
        except Exception:               # noqa: BLE001
            time.sleep(0.3)
    return False


def summarise(cfg) -> dict:
    """跑完之后从证据包目录汇总三项增益指标。

    **必须按 verdict 分组。**FALSE_ALARM 的 delta_conf 是负值（复核把一个
    0.41 的误检压到 0.05），与真缺陷混在一起算均值会接近零，看上去像复核
    没起作用——ICD §6.4 特意警告过这一点。
    """
    root = Path(cfg.get("uploader.evidence_dir", "evidence"))
    by: dict[str, list] = {}
    total = ok = 0
    for mf in sorted(root.glob("*/*/manifest.json")):
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        v = m["verdict"]["result"]
        g = m["gain"]
        by.setdefault(v, []).append(g)
        total += 1
        ok += 1 if g["verify_success"] else 0
    out = {"total": total,
           "verify_success_rate": round(ok / total, 4) if total else 0.0,
           "by_verdict": {}}
    real_d, real_n = 0.0, 0
    for v, gs in sorted(by.items()):
        d = sum(g["delta_conf"] for g in gs) / len(gs)
        r = sum(g["pixel_density_ratio"] for g in gs) / len(gs)
        out["by_verdict"][v] = {"n": len(gs), "avg_delta_conf": round(d, 4),
                                "avg_density_ratio": round(r, 4)}
        if v in ("CONFIRMED_DEFECT", "READING_ABNORMAL"):
            real_d += d * len(gs)
            real_n += len(gs)
    out["delta_conf_on_real_defects"] = round(real_d / real_n, 4) if real_n else None
    return out


def print_report(cfg, summary: dict) -> None:
    print("\n" + "=" * 68)
    print("一轮巡检小结")
    print("=" * 68)
    print("证据包 %d 个，复核成功率 %.1f %%（目标 > 85 %%）"
          % (summary["total"], summary["verify_success_rate"] * 100))
    if summary["by_verdict"]:
        print("\n%-20s %5s %12s %12s" % ("结论", "条数", "平均Δconf", "平均密度比"))
        for v, s in summary["by_verdict"].items():
            print("%-20s %5d %12.4f %12.4f"
                  % (v, s["n"], s["avg_delta_conf"], s["avg_density_ratio"]))
    d = summary["delta_conf_on_real_defects"]
    print("\n真缺陷组 Δconf 均值 = %s（目标 > +0.25）"
          % ("%.4f" % d if d is not None else "本轮无真缺陷样本"))
    print("提醒：FALSE_ALARM 组的 Δconf 为负是正常的，"
          "与真缺陷混在一起算均值会接近零（ICD §6.4）")
    print("\n证据包目录 %s/    日志 %s/"
          % (cfg.get("uploader.evidence_dir"), cfg.get("logging.dir", "logs")))
    if not cfg.get("_no_cloud", False):
        print("台账网页 http://%s:%s/" % (cfg.get("cloud.host"), cfg.get("cloud.port")))


def main() -> int:
    ap = argparse.ArgumentParser(description="一键起全系统")
    ap.add_argument("--config", default=None)
    ap.add_argument("--seconds", type=float, default=0.0, help="0 表示跑到 Ctrl-C")
    ap.add_argument("--no-cloud", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="子进程日志不打到终端")
    a = ap.parse_args()

    from patrol.common.config import Config
    cfg = Config.load(a.config)
    env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONUNBUFFERED="1")
    if a.config:
        env["PATROL_CONFIG"] = a.config

    procs: list[tuple[str, subprocess.Popen]] = []
    out = subprocess.DEVNULL if a.quiet else None

    if not a.no_cloud:
        p = subprocess.Popen([sys.executable, "-m", "cloud.server"], cwd=REPO,
                             env=env, stdout=out, stderr=out)
        procs.append(("cloud", p))
        url = "http://%s:%s/healthz" % (cfg.get("cloud.host"), cfg.get("cloud.port"))
        if not wait_http(url):
            print("云端未能在 15 s 内就绪，继续跑边缘端")
        else:
            print("云端就绪  http://%s:%s/" % (cfg.get("cloud.host"), cfg.get("cloud.port")))

    for name, cmd in NODES:
        p = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=out, stderr=out)
        procs.append((name, p))
        print("已启动 %s (pid %d)" % (name, p.pid))
        # 网关要先绑定端口，感知与任务再连上来
        time.sleep(1.2 if name == "gateway" else 0.4)

    print("\n全系统运行中。Ctrl-C 收工。")
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    t0 = time.time()
    try:
        while not stop["flag"]:
            time.sleep(0.5)
            dead = [(n, p) for n, p in procs if p.poll() is not None]
            for n, p in dead:
                print("!! %s 退出，返回码 %s" % (n, p.returncode))
                procs.remove((n, p))
            if not procs:
                break
            if a.seconds and time.time() - t0 >= a.seconds:
                break
    finally:
        # mission 先停：停了心跳，网关看门狗会介入让车走完路线，
        # 这正是设计要的行为，顺便把这条路径也跑一遍
        order = ["mission", "perception", "uploader", "cloud", "gateway"]
        for name in order:
            for n, p in list(procs):
                if n != name:
                    continue
                p.terminate()
                try:
                    p.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    p.kill()
                procs.remove((n, p))
        cfg._d["_no_cloud"] = a.no_cloud       # noqa: SLF001
        print_report(cfg, summarise(cfg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
