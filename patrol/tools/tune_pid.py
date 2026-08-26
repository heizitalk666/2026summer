#!/usr/bin/env python3
"""云台 PID 整定与阶跃响应。方案书 §6.3.3 / §9.1 第六级 / §11.1 交付物。

    python -m patrol.tools.tune_pid --out out/pid          # 整定 + 出曲线
    python -m patrol.tools.tune_pid --out out/pid --compare-gain-schedule

闭环结构（与真机完全一致，只是被控对象是 ptz_stub）：

    目标世界方向 ──► 由当前云台位姿投影出像素坐标 ──► PID ──► set_rate()
         ▲                                                      │
         └──────────────── 云台真实转动（含加速度限、齿隙、抖动）◄─┘

**被控对象是真的。**ptz_stub 有角速度上限、角加速度 240 °/s²、齿隙 0.05°、
到位抖动 0.15°，所以整定出来的参数、超调量、调节时间都是实测值，不是仿真
出来的漂亮数字。

整定用临界比例度法（齐格勒-尼科尔斯）：
  1. 置 Ki = Kd = 0，只投比例，变焦固定为 1
  2. 逐步增大 Kp，观察阶跃响应
  3. 出现等幅振荡时记下临界比例系数 Kcr 与振荡周期 Tcr
  4. Kp = 0.6·Kcr，Ti = 0.5·Tcr，Td = 0.125·Tcr，再换算成 Ki、Kd
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

from patrol.common.config import Config
from patrol.drivers.base import PTZSpeed
from patrol.drivers.stub.ptz_stub import PTZStub
from patrol.mission.servo import AxisPID, GimbalServo, PIDGains
from patrol.scene.optics import hfov_at_zoom


def pixel_of(target_pan_deg: float, cur_pan_deg: float, *, zoom: float,
             hfov_1x: float, width: int) -> float:
    """目标在画面上的横坐标。

    目标方向与云台方向的夹角 φ 投影到像素：x = W/2 + f·tan(φ)，
    f = W / (2·tan(hfov/2))。这就是针孔投影，与 scene/optics 同源。
    """
    hfov = hfov_at_zoom(hfov_1x, zoom)
    f = width / (2.0 * math.tan(math.radians(hfov) / 2.0))
    phi = math.radians(target_pan_deg - cur_pan_deg)
    # 目标转到画面右侧对应 pan 减小，故取负号（与 aim_offset 的增量语义一致）
    return width / 2.0 - f * math.tan(phi)


def closed_loop(cfg, *, gains: PIDGains, step_deg: float = 12.0,
                zoom: float = 1.0, gain_schedule: bool = True,
                duration_s: float = 4.0, seed: int = 0,
                use_rate: bool = True) -> tuple[AxisPID, list[dict]]:
    """跑一次闭环阶跃响应。返回 (pan 通道的 PID, 逐周期轨迹)。"""
    ptz = PTZStub(cfg, seed=seed)
    W = int(cfg.get("camera.width", 1920))
    hfov1x = float(cfg.get("optics.hfov_at_1x_deg", 60.0))
    s = cfg.get("mission.servo")
    period = float(s.get("period_ms", 100)) / 1000.0
    ttl = int(s.get("rate_ttl_ms", 300))

    pid = AxisPID(gains, period_s=period,
                  omega_max_dps=ptz.capabilities().max_pan_dps,
                  hfov_at_1x_deg=hfov1x, image_span_px=W,
                  integral_separation_px=float(s.get("integral_separation_px", 150.0)),
                  integral_max=float(s.get("integral_max", 500.0)),
                  alpha=float(s.get("alpha", 0.6)),
                  gain_schedule=gain_schedule)

    # 先把云台稳在 0 位并设好变焦
    ptz.set_pose(0.0, 0.0, zoom, PTZSpeed.NORMAL)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 3.0 and not ptz.status().at_target:
        time.sleep(0.01)

    traj: list[dict] = []
    t_start = time.monotonic()
    while True:
        t = time.monotonic() - t_start
        if t > duration_s:
            break
        st = ptz.status()
        cx = pixel_of(step_deg, st.pan_deg, zoom=st.zoom, hfov_1x=hfov1x, width=W)
        e = W / 2.0 - cx
        omega = pid.step(e, zoom=st.zoom, t_s=t)
        if use_rate:
            ptz.set_rate(omega, 0.0, ttl)
        else:
            # 备选乙：位置增量多轮闭环（差异清单 A1 的退路）
            ptz.set_pose(float(np.clip(st.pan_deg + omega * period, -170, 170)),
                         0.0, zoom, PTZSpeed.NORMAL)
        traj.append({"t_s": round(t, 4), "pan_deg": round(st.pan_deg, 4),
                     "error_px": round(e, 2), "omega_dps": round(omega, 3),
                     "zoom": round(st.zoom, 3)})
        time.sleep(max(0.0, period - (time.monotonic() - t_start - t)))
    ptz.close()
    return pid, traj


def find_critical_gain(cfg, *, seed: int = 0) -> dict:
    """临界比例度法：只投比例，逐步增大 Kp 直到出现等幅振荡。"""
    rows = []
    kcr = tcr = None
    for kp in (0.05, 0.10, 0.20, 0.35, 0.55, 0.80, 1.10, 1.50, 2.00, 2.60):
        pid, traj = closed_loop(cfg, gains=PIDGains(kp=kp, ki=0.0, kd=0.0),
                                seed=seed, duration_s=3.5)
        e = np.array([r["error_px"] for r in traj], float)
        t = np.array([r["t_s"] for r in traj], float)
        # 数过零次数判断是否起振；用后半段的包络比判断是否等幅
        zc = int(np.count_nonzero(np.diff(np.sign(e)) != 0))
        half = len(e) // 2
        env_early = float(np.max(np.abs(e[:half]))) if half else 0.0
        env_late = float(np.max(np.abs(e[half:]))) if half else 0.0
        ratio = env_late / max(1e-6, env_early)
        m = pid.metrics()
        rows.append({"kp": kp, "zero_crossings": zc, "envelope_ratio": round(ratio, 3),
                     "overshoot_pct": m.get("overshoot_pct"),
                     "settling_time_s": m.get("settling_time_s"),
                     "steady_error_px": m.get("steady_error_px")})
        if kcr is None and zc >= 4 and ratio > 0.55:
            kcr = kp
            if zc >= 2:
                tcr = float(2.0 * np.mean(np.diff(t[np.where(np.diff(np.sign(e)) != 0)])))
    out = {"sweep": rows, "kcr": kcr, "tcr": tcr}
    if kcr and tcr:
        kp = 0.6 * kcr
        ti = 0.5 * tcr
        td = 0.125 * tcr
        out["ziegler_nichols"] = {
            "kp": round(kp, 4), "ti_s": round(ti, 4), "td_s": round(td, 4),
            "ki": round(kp / ti, 4), "kd": round(kp * td, 4)}
    return out


def plot_step(trajs: dict[str, list[dict]], out: Path, *, deadband_px: float,
              title: str) -> None:
    """阶跃响应曲线。横轴时间，纵轴像素偏差。"""
    W, H = 920, 520
    img = np.full((H, W, 3), 250, np.uint8)
    ml, mr, mt, mb = 80, 180, 54, 62
    allv = [r["error_px"] for tr in trajs.values() for r in tr]
    tmax = max((r["t_s"] for tr in trajs.values() for r in tr), default=1.0)
    lo, hi = min(allv + [0]), max(allv + [0])
    pad = 0.10 * max(1e-6, hi - lo)
    lo, hi = lo - pad, hi + pad
    px = lambda v: int(ml + v / max(1e-9, tmax) * (W - ml - mr))     # noqa: E731
    py = lambda v: int(H - mb - (v - lo) / max(1e-9, hi - lo) * (H - mt - mb))  # noqa: E731

    cv2.rectangle(img, (ml, mt), (W - mr, H - mb), (218, 218, 218), 1)
    cv2.line(img, (ml, py(0)), (W - mr, py(0)), (200, 200, 200), 1)
    for band in (deadband_px, -deadband_px):
        cv2.line(img, (ml, py(band)), (W - mr, py(band)), (170, 205, 170), 1, cv2.LINE_AA)
    cv2.putText(img, "+/-%.0f px band" % deadband_px, (W - mr + 8, py(deadband_px) + 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (90, 150, 90), 1, cv2.LINE_AA)

    colors = [(190, 110, 60), (60, 60, 200), (60, 150, 60), (150, 60, 160)]
    for k, (name, tr) in enumerate(trajs.items()):
        c = colors[k % len(colors)]
        pts = [(px(r["t_s"]), py(r["error_px"])) for r in tr]
        for i in range(1, len(pts)):
            cv2.line(img, pts[i - 1], pts[i], c, 2, cv2.LINE_AA)
        cv2.putText(img, name, (W - mr + 8, mt + 22 + 22 * k),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.46, c, 1, cv2.LINE_AA)
    for i in range(5):
        tv = tmax * i / 4
        cv2.putText(img, "%.1fs" % tv, (px(tv) - 14, H - mb + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, (100, 100, 100), 1, cv2.LINE_AA)
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        cv2.putText(img, "%.0f" % v, (14, py(v) + 5), cv2.FONT_HERSHEY_SIMPLEX,
                    0.44, (100, 100, 100), 1, cv2.LINE_AA)
    cv2.putText(img, title, (ml, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                (40, 40, 40), 1, cv2.LINE_AA)
    cv2.putText(img, "pixel error", (12, mt - 14), cv2.FONT_HERSHEY_SIMPLEX,
                0.44, (100, 100, 100), 1, cv2.LINE_AA)
    cv2.imwrite(str(out), img)


def main() -> int:
    ap = argparse.ArgumentParser(description="云台 PID 整定与阶跃响应")
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default="out/pid")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tune", action="store_true", help="跑临界比例度法整定")
    ap.add_argument("--compare-gain-schedule", action="store_true",
                    help="对比开/关增益调度在 3× 变焦下的表现")
    a = ap.parse_args()

    cfg = Config.load(a.config)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    s = cfg.get("mission.servo")
    gains = PIDGains(kp=float(s["kp"]), ki=float(s["ki"]), kd=float(s["kd"]))
    deadband = float(s.get("deadband_px", 20.0))
    report: dict = {"gains": gains.__dict__, "deadband_px": deadband}

    if a.tune:
        print("临界比例度法整定中（只投比例，逐步增大 Kp）…")
        tune = find_critical_gain(cfg, seed=a.seed)
        report["tuning"] = tune
        print("  %-6s %-6s %-8s %-9s %-9s" % ("Kp", "过零", "包络比", "超调%", "调节时间s"))
        for r in tune["sweep"]:
            print("  %-6.2f %-6d %-8.3f %-9s %-9s"
                  % (r["kp"], r["zero_crossings"], r["envelope_ratio"],
                     r["overshoot_pct"], r["settling_time_s"]))
        if tune.get("ziegler_nichols"):
            zn = tune["ziegler_nichols"]
            print("  Kcr=%.2f Tcr=%.3fs → Kp=%.3f Ki=%.3f Kd=%.3f"
                  % (tune["kcr"], tune["tcr"], zn["kp"], zn["ki"], zn["kd"]))
        else:
            print("  未在扫描范围内观察到等幅振荡（对象阻尼较大），沿用配置参数")

    trajs: dict[str, list[dict]] = {}
    for zoom in (1.0, 3.0):
        pid, tr = closed_loop(cfg, gains=gains, zoom=zoom, seed=a.seed)
        trajs["zoom %.0fx" % zoom] = tr
        m = pid.metrics(deadband_px=deadband)
        report["step_z%.0f" % zoom] = m
        print("\n阶跃 12° @ zoom=%.0fx  超调 %.1f%%  调节时间 %ss  稳态 %.1f px  %s"
              % (zoom, m.get("overshoot_pct", -1), m.get("settling_time_s"),
                 m.get("steady_error_px", -1),
                 "达标" if pid.meets_spec(deadband_px=deadband) else "超差"))

    if a.compare_gain_schedule:
        pid_off, tr_off = closed_loop(cfg, gains=gains, zoom=3.0,
                                      gain_schedule=False, seed=a.seed)
        trajs["zoom 3x 关调度"] = tr_off
        m = pid_off.metrics(deadband_px=deadband)
        report["step_z3_no_schedule"] = m
        print("\n关掉增益调度 @ zoom=3x  超调 %.1f%%  调节时间 %ss  稳态 %.1f px"
              % (m.get("overshoot_pct", -1), m.get("settling_time_s"),
                 m.get("steady_error_px", -1)))
        print("  → 这就是 §6.3.2 说的：不做调度时控制量是所需值的 3 倍")

    plot_step(trajs, out / "step_response.png", deadband_px=deadband,
              title="gimbal pan step response  12 deg")
    with open(out / "step_response.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["series", "t_s", "pan_deg", "error_px", "omega_dps", "zoom"])
        for name, tr in trajs.items():
            for r in tr:
                w.writerow([name, r["t_s"], r["pan_deg"], r["error_px"],
                            r["omega_dps"], r["zoom"]])
    (out / "pid_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n已写出 %s/（step_response.png / .csv / pid_report.json）" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
