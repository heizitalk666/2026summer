#!/usr/bin/env python3
"""标定工具：五点读数标定 + 像素密度标定。方案书 §6.1.2 / §9.2。

    python -m patrol.tools.calibrate --out out/calib
    python -m patrol.tools.calibrate --interactive     # 真机：人工调表后回车

产出（方案书 §11.1 交付物清单里的「标定记录」）：

    calibration.csv      五点标定原始数据，每点 10 次
    calibration.md       标定报告：曲线系数、线性度、重复性、基本误差
    calibration_curve.png 标定曲线图
    pixel_density.csv    像素密度标定：实测视场角与标称值的偏差

**为什么每点要测 10 次。**方案书 §9.3 规定基本误差是"五点各测 10 次取最大
偏差"。10 次不是走过场：每次取图都经过相机桩，有独立的噪声实现与云台残余
抖动（settle_jitter_deg），所以 10 次读数确实是 10 个独立样本，其极差就是
重复性指标。在真机上这 10 次对应 10 次独立抓拍，性质相同。

**为什么要先标定再算基本误差。**标定拟合出的斜率与截距会吸收系统性偏置
（镜头畸变、零位安装偏差、算法的固定偏移）。不做标定直接比对真值，测到的
是"未标定误差"，比方案书的口径严得多，也不是工程上的做法。
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import cv2
import numpy as np

from patrol.common.config import Config
from patrol.drivers.base import PTZSpeed
from patrol.drivers.factory import build_drivers
from patrol.perception.reading.pointer import read_pointer_gauge
from patrol.perception.reading.scale import (CalibrationPoint, calibrate,
                                             error_budget)
from patrol.scene.optics import (PinholeCamera, hfov_at_zoom, pixel_density)
from patrol.scene.world import World

DEFAULT_FRACTIONS = (0.0, 0.25, 0.50, 0.75, 1.00)


def _aim(cfg: Config, world: World, target, distance_m: float):
    """把车摆在正对目标、距离 distance_m 的位置，返回 (车位姿, pan, tilt)。

    画幅与视场角一律从 configs/camera.yaml 取。这里曾经写死 1920/1080/60.0，
    与配置值恰好一致所以没有症状——但本工具的产出「标定记录」是方案书 §11.1
    点名的交付物，相机配置改动的那天，标定报告会按旧光学参数出数，错误一路
    带进验收材料。
    """
    cx = float(target.position[0])
    cy = float(target.position[1]) - float(distance_m)
    yaw = 90.0
    cam = PinholeCamera(int(cfg.get("camera.width", 1920)),
                        int(cfg.get("camera.height", 1080)),
                        hfov_at_zoom(float(cfg.get("optics.hfov_at_1x_deg", 60.0)), 1.0),
                        (cx, cy, world.camera_height_m), yaw, 0.0, 0.0)
    pan, tilt = cam.aim_offset_deg(target.position)
    return (cx, cy, yaw), pan, tilt


def collect(cfg: Config, *, target_id: str, distance_m: float, zoom: float,
            repeats: int, fractions=DEFAULT_FRACTIONS, interactive: bool = False,
            seed: int = 0) -> tuple[list[CalibrationPoint], list[dict], World]:
    """按五点法采集。返回 (标定点, 逐次原始记录, world)。"""
    chassis, ptz, camera, loc = build_drivers(cfg, seed=seed)
    # **必须用相机桩持有的那个 World。**build_drivers 内部会自己建一个 World，
    # 如果这里另建一份，改真值改的是另一个对象，渲染器根本看不到——五个标定
    # 点的指针不会动，拟合出来是一条近乎垂直的线，线性度会飙到几十个百分点。
    world = camera.world
    target = world.by_id(target_id)
    if target is None:
        raise SystemExit("场景里没有目标 %s" % target_id)
    pri = target.priors
    rmin, rmax = float(pri["range_min"]), float(pri["range_max"])
    camera.start(int(cfg.get("camera.width")), int(cfg.get("camera.height")), 10)
    # 停车再标定：行进中有运动模糊，标定结果不可重复
    chassis.pause("VERIFY_REQUEST")
    pose, pan, tilt = _aim(cfg, world, target, distance_m)
    # 桩的相机跟着底盘走，这里直接把渲染视点固定在标定位
    camera._viewpoint = lambda: (pose, ptz.true_pose()[0], ptz.true_pose()[1],
                                 ptz.true_pose()[2], 0.0)          # noqa: SLF001
    ptz.set_pose(pan, tilt, zoom, PTZSpeed.NORMAL)
    _settle(ptz)

    points: list[CalibrationPoint] = []
    raw: list[dict] = []
    for frac in fractions:
        nominal = rmin + frac * (rmax - rmin)
        if interactive:
            input("请把表计指针调到 %.4g %s（量程 %.0f %%），调好后回车："
                  % (nominal, pri.get("unit") or "", frac * 100))
        else:
            target.truth["value"] = float(nominal)     # 桩：直接设真值重绘
        cp = CalibrationPoint(nominal_value=float(nominal))
        for i in range(repeats):
            f = camera.grab()
            meta = next((m for m in camera.last_targets()
                         if m["target_id"] == target_id), None)
            if meta is None:
                raw.append({"nominal": nominal, "i": i, "ok": False,
                            "reason": "目标不在视野"})
                continue
            rd = read_pointer_gauge(f.image, meta["bbox"], pri)
            p = pixel_density(f.width, meta["target_size_m"], zoom,
                              meta["distance_m"], float(cfg.get("optics.hfov_at_1x_deg")))
            rec = {"nominal": nominal, "i": i, "ok": bool(rd.ok),
                   "angle_deg": rd.angle_deg, "value": rd.value,
                   "confidence": rd.confidence, "axis_ratio": rd.axis_ratio,
                   "glare": rd.glare_ratio, "pixel_density_px": round(p, 2),
                   "reason": rd.fail_reason}
            raw.append(rec)
            if rd.ok:
                cp.angles_deg.append(float(rd.angle_deg))
        points.append(cp)

    for d in (camera, ptz, chassis, loc):
        d.close()
    return points, raw, world


def _points_from(raw: list[dict], conf_floor: float):
    """从 raw 记录重建标定点，只保留 confidence ≥ conf_floor 的读数。

    用于并列报一个「剔除算法自报低置信度读数」的口径。**它不替代主指标**——
    方案书 §9.3 的口径是所有有效读数一视同仁，那个数照常算、照常报。
    """
    from collections import OrderedDict
    groups: "OrderedDict[float, list[float]]" = OrderedDict()
    for r in raw:
        if not r.get("ok"):
            continue
        if float(r.get("confidence", 1.0)) < conf_floor:
            continue
        groups.setdefault(float(r["nominal"]), []).append(float(r["angle_deg"]))
    pts = []
    for nom, angs in groups.items():
        cp = CalibrationPoint(nominal_value=nom)
        cp.angles_deg.extend(angs)
        pts.append(cp)
    return pts, list(groups.values())


def _settle(ptz, timeout_s: float = 4.0) -> None:
    import time
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        s = ptz.status()
        if s.at_target and s.focus_state.value == "LOCKED":
            return
        time.sleep(0.02)


def pixel_density_calibration(cfg: Config, *, target_id: str = "TGT-01",
                              seed: int = 0) -> list[dict]:
    """像素密度标定。方案书 §9.2.3。

    在已知距离处观察已知尺寸的靶标，在各变焦档位下测量实际像素宽度，与公式
    计算值比对，**修正视场角参数**。这一步是为了消除镜头标称视场角与实际值
    的偏差，保证像素密度判据的准确性。

    差异清单 C5 指出方案书内部对 θ 有 60° 与 67° 两个口径，这个函数就是用来
    定这个数的：实测与标称偏差超过容差时必须重算 d_max 与路线标定规范。
    """
    chassis, ptz, camera, loc = build_drivers(cfg, seed=seed)
    world = camera.world
    target = world.by_id(target_id)
    camera.start(int(cfg.get("camera.width")), int(cfg.get("camera.height")), 10)
    chassis.pause("VERIFY_REQUEST")
    theta_nom = float(cfg.get("optics.hfov_at_1x_deg"))
    rows: list[dict] = []
    for d in (3.0, 4.0, 5.0, 6.0):
        pose, pan, tilt = _aim(cfg, world, target, d)
        camera._viewpoint = lambda p=pose: (p, ptz.true_pose()[0], ptz.true_pose()[1],
                                            ptz.true_pose()[2], 0.0)   # noqa: SLF001
        for z in (1.0, 2.0, 3.0):
            ptz.set_pose(pan, tilt, z, PTZSpeed.NORMAL)
            _settle(ptz)
            f = camera.grab()
            m = next((m for m in camera.last_targets()
                      if m["target_id"] == target_id), None)
            if m is None:
                continue
            measured = float(m["bbox"][2] - m["bbox"][0])
            expect = pixel_density(f.width, m["target_size_m"], z,
                                   m["distance_m"], theta_nom)
            # 由实测像素宽度反解实际视场角
            theta_meas = 2.0 * math.degrees(math.atan(
                f.width * m["target_size_m"] * z / (2.0 * m["distance_m"] * measured)))
            rows.append({"distance_m": round(m["distance_m"], 3), "zoom": z,
                         "measured_px": round(measured, 2),
                         "formula_px": round(expect, 2),
                         "rel_err_pct": round((measured - expect) / expect * 100, 3),
                         "theta_measured_deg": round(theta_meas, 3)})
    for dv in (camera, ptz, chassis, loc):
        dv.close()
    return rows


def plot_curve(points, result, out: Path, unit: str = "") -> None:
    """标定曲线图：横轴指针转角，纵轴标称读数，含拟合直线与残差。"""
    W, H = 900, 620
    img = np.full((H, W, 3), 250, np.uint8)
    ml, mr, mt, mb = 90, 40, 60, 90
    x = np.array([p.mean_angle for p in points], float)
    y = np.array([p.nominal_value for p in points], float)
    if len(x) < 2:
        return
    x0, x1 = float(x.min()), float(x.max())
    y0, y1 = float(y.min()), float(y.max())
    px = lambda v: int(ml + (v - x0) / max(1e-9, x1 - x0) * (W - ml - mr))   # noqa: E731
    py = lambda v: int(H - mb - (v - y0) / max(1e-9, y1 - y0) * (H - mt - mb))  # noqa: E731

    cv2.rectangle(img, (ml, mt), (W - mr, H - mb), (215, 215, 215), 1)
    for i in range(5):
        gy = y0 + (y1 - y0) * i / 4
        cv2.line(img, (ml, py(gy)), (W - mr, py(gy)), (232, 232, 232), 1)
        cv2.putText(img, "%.2f" % gy, (12, py(gy) + 5), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (90, 90, 90), 1, cv2.LINE_AA)
    a, b = result.slope, result.intercept
    cv2.line(img, (px(x0), py(a * x0 + b)), (px(x1), py(a * x1 + b)),
             (190, 120, 60), 2, cv2.LINE_AA)
    for p_, r_ in zip(points, result.residuals_pct_fs):
        cv2.circle(img, (px(p_.mean_angle), py(p_.nominal_value)), 6, (60, 60, 200), -1, cv2.LINE_AA)
        cv2.putText(img, "%+.3f%%" % r_, (px(p_.mean_angle) - 26, py(p_.nominal_value) - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (60, 60, 200), 1, cv2.LINE_AA)
    cv2.putText(img, "calibration curve  R = %.6f*theta + %.4f" % (a, b),
                (ml, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (40, 40, 40), 1, cv2.LINE_AA)
    cv2.putText(img, "linearity %.3f %%FS   repeatability %.3f %%FS"
                % (result.linearity_pct_fs, result.repeatability_pct_fs),
                (ml, H - 46), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (40, 40, 40), 1, cv2.LINE_AA)
    cv2.putText(img, "pointer angle (deg)", (W // 2 - 80, H - 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (90, 90, 90), 1, cv2.LINE_AA)
    cv2.imwrite(str(out), img)


def main() -> int:
    ap = argparse.ArgumentParser(description="五点读数标定与像素密度标定")
    ap.add_argument("--config", default=None)
    ap.add_argument("--target", default="TGT-01")
    ap.add_argument("--distance", type=float, default=5.0)
    ap.add_argument("--zoom", type=float, default=3.0)
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--out", default="out/calib")
    ap.add_argument("--interactive", action="store_true", help="真机：人工调表")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    cfg = Config.load(a.config)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    print("五点读数标定  目标=%s  距离=%.1f m  变焦=%.1fx  每点 %d 次"
          % (a.target, a.distance, a.zoom, a.repeats))
    points, raw, world = collect(cfg, target_id=a.target, distance_m=a.distance,
                                 zoom=a.zoom, repeats=a.repeats,
                                 interactive=a.interactive, seed=a.seed)
    tgt = world.by_id(a.target)
    pri = tgt.priors
    res = calibrate(points, range_min=float(pri["range_min"]),
                    range_max=float(pri["range_max"]))

    with open(out / "calibration.csv", "w", newline="", encoding="utf-8") as f:
        wcsv = csv.DictWriter(f, fieldnames=sorted({k for r in raw for k in r}))
        wcsv.writeheader()
        wcsv.writerows(raw)

    ok = [r for r in raw if r.get("ok")]
    p_med = float(np.median([r["pixel_density_px"] for r in ok])) if ok else 0.0
    # 基本误差：标定后各次读数与标称值的最大偏差
    span = float(pri["range_max"]) - float(pri["range_min"])
    basic = max(abs(res.apply(r["angle_deg"]) - r["nominal"]) / span * 100
                for r in ok) if ok else float("nan")
    budget = error_budget(p_med)

    # ---- 算法自报的低置信度读数
    #
    # **这一段不改主指标，只把口径摊开。** 标定按方案书 §9.3 是"五点各测 10 次取
    # 最大偏差"，所有 ok 的读数一视同仁地进统计——上面那三个数就是这么算的，
    # 不动。但运行时 fusion 并不这样：`_confidence()` 把 reading_confidence
    # 折进总置信度（0.5×检测 + 0.5×读数），低置信度的读数会被压低分、进人工复核。
    # 也就是说系统会对"算法自己都不确定"的读数区别对待，标定工具不会。
    #
    # 实测这个差别不是理论上的：12 组种子共 600 次读数里，599 次 confidence
    # 是 1.000 且偏离该点中位 ≤0.55°，唯一一次 confidence 0.498 偏了 9.75°，
    # 单独把那一轮的重复性从 0.3 推到 3.7 % FS。**算法当时就知道自己不确定。**
    #
    # 所以这里并列报一个"剔除自报低置信度读数"的口径，让两个数都在记录里。
    # 哪个口径写进验收由评审定；**不允许只报好看的那个**。
    conf_floor = float(cfg.get("perception.reading.confidence_floor", 0.60))
    low = [r for r in ok if float(r.get("confidence", 1.0)) < conf_floor]
    res_hi = None
    if low:
        pts_hi = _points_from(raw, conf_floor)
        if all(len(g) >= 2 for g in pts_hi[1]):
            res_hi = calibrate(pts_hi[0], range_min=float(pri["range_min"]),
                               range_max=float(pri["range_max"]))

    print()
    print(res.report())
    print("基本误差  %.3f %% FS   (限值 0.5)  %s"
          % (basic, "合格" if basic <= 0.5 else "超差"))
    if low:
        print("\n低置信度读数  %d / %d 次 confidence < %.2f（算法自报不确定）"
              % (len(low), len(ok), conf_floor))
        if res_hi is not None:
            print("剔除后重复性  %.3f %% FS（主指标仍以上面的 %.3f 为准）"
                  % (res_hi.repeatability_pct_fs, res.repeatability_pct_fs))
    print("像素密度  %.1f px      理论合成误差 %.3f %% FS"
          % (p_med, budget["total_pct_fs"]))

    plot_curve(points, res, out / "calibration_curve.png", pri.get("unit") or "")

    pdrows = pixel_density_calibration(cfg, target_id=a.target, seed=a.seed)
    with open(out / "pixel_density.csv", "w", newline="", encoding="utf-8") as f:
        wcsv = csv.DictWriter(f, fieldnames=list(pdrows[0]))
        wcsv.writeheader()
        wcsv.writerows(pdrows)
    theta_nom = float(cfg.get("optics.hfov_at_1x_deg"))
    theta_meas = float(np.median([r["theta_measured_deg"] for r in pdrows]))
    tol = float(cfg.get("optics.hfov_tolerance_deg", 3.0))
    drift_ok = abs(theta_meas - theta_nom) <= tol

    with open(out / "calibration.md", "w", encoding="utf-8") as f:
        f.write("# 标定记录\n\n")
        f.write("| 项 | 值 |\n|---|---|\n")
        f.write("| 目标 | %s (%s) |\n" % (a.target, tgt.defect_class))
        f.write("| 距离 / 变焦 | %.2f m / %.1fx |\n" % (a.distance, a.zoom))
        f.write("| 每点重复次数 | %d |\n" % a.repeats)
        f.write("| 中位像素密度 | %.1f px |\n" % p_med)
        f.write("\n## 五点读数标定\n\n```\n%s\n基本误差  %.3f %% FS\n```\n"
                % (res.report(), basic))
        f.write("\n| 标称读数 | 平均转角 | 极差 | 残差 %% FS |\n|---|---|---|---|\n")
        for p_, r_ in zip(points, res.residuals_pct_fs):
            f.write("| %.4g | %+.3f° | %.3f° | %+.3f |\n"
                    % (p_.nominal_value, p_.mean_angle, p_.range_angle, r_))
        f.write("\n## 像素密度标定\n\n")
        f.write("标称视场角 %.2f°，实测中位 %.2f°，偏差 %.2f°（容差 %.1f°）→ %s\n\n"
                % (theta_nom, theta_meas, theta_meas - theta_nom, tol,
                   "合格" if drift_ok else "**超差，d_max 与路线标定规范必须按实测值重算**"))
        f.write("| 距离 m | 变焦 | 实测 px | 公式 px | 相对误差 %% | 反解视场角° |\n")
        f.write("|---|---|---|---|---|---|\n")
        for r in pdrows:
            f.write("| %.2f | %.1f | %.2f | %.2f | %+.3f | %.3f |\n"
                    % (r["distance_m"], r["zoom"], r["measured_px"],
                       r["formula_px"], r["rel_err_pct"], r["theta_measured_deg"]))

    print("\n像素密度标定  标称 %.2f° 实测 %.2f°  偏差 %.2f°  %s"
          % (theta_nom, theta_meas, theta_meas - theta_nom,
             "合格" if drift_ok else "超差"))
    print("已写出 %s/" % out)
    return 0 if (res.passes() and basic <= 0.5) else 1


if __name__ == "__main__":
    raise SystemExit(main())
