#!/usr/bin/env python3
"""虚拟配电室实时预览。

    python -m patrol.tools.viewer                    # 有显示器：开窗口，键盘控云台
    python -m patrol.tools.viewer --headless --out out/   # 无显示器：存帧
    python -m patrol.tools.viewer --demo-zoom --out out/  # 存一张变焦对比图

键位：A/D 左右转，W/S 俯仰，Q/E 变焦，空格 暂停/恢复行驶，
      H 云台归位，R 强制安全事件，L 强制定位失锁，ESC 退出。

画面上每个目标标出 **像素密度 p**：低于 120 px 判据画橙框（读不准，必须
复核），达标画绿框。这一幕就是全项目的立论——巡航态 5 m 处表盘只有 50 px，
3× 变焦后 150 px。
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import cv2
import numpy as np

from patrol.common.config import Config
from patrol.drivers.base import ExecProgress, PTZSpeed, selftest
from patrol.drivers.factory import build_drivers
from patrol.scene.optics import pixel_density

PMIN = 120.0


def annotate(img, targets, zoom, cfg, chassis, ptz, pose, hint=""):
    w = img.shape[1]
    hfov1x = float(cfg.get("optics.hfov_at_1x_deg", 60.0))
    for m in targets:
        x1, y1, x2, y2 = [int(round(v)) for v in m["bbox"]]
        p = pixel_density(w, m["target_size_m"], zoom, m["distance_m"], hfov1x)
        ok = p >= PMIN
        col = (110, 210, 110) if ok else (80, 140, 240)
        cv2.rectangle(img, (x1, y1), (x2, y2), col, 2)
        cv2.putText(img, "%s p=%.0fpx d=%.2fm %s" % (
            m["defect_class"], p, m["distance_m"], "达标" if ok else "需复核"),
            (x1, max(18, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)

    st, ps = chassis.status(), ptz.status()
    bar = [
        "底盘 %-8s v=%.2f m/s  电量 %.1f%%  路线 %.0f%%" % (
            st.state.value, st.speed_mps, st.battery_pct, st.path_progress * 100),
        "云台 pan=%+7.2f tilt=%+6.2f zoom=%.2fx hfov=%.1f°  focus=%s at_target=%s" % (
            ps.pan_deg, ps.tilt_deg, ps.zoom, ps.hfov_deg, ps.focus_state.value, ps.at_target),
        "位姿 (%.2f, %.2f) yaw=%+6.1f°  %s  valid=%s" % (
            pose.x_m, pose.y_m, pose.yaw_deg, pose.source.value, pose.valid),
    ]
    if hint:
        bar.append(hint)
    y = 30
    for line in bar:
        cv2.putText(img, line, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                    (20, 20, 20), 4, cv2.LINE_AA)
        cv2.putText(img, line, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                    (235, 235, 235), 1, cv2.LINE_AA)
        y += 30
    if st.safety_layer_active:
        cv2.putText(img, "! 底盘安全层介入", (16, y + 6), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (60, 60, 245), 2, cv2.LINE_AA)
    return img


def demo_zoom(cfg, out: Path) -> None:
    """把最关键的一幕存成对比图：同一块表，z=1 与 z=3。"""
    from patrol.scene.render import RenderOptions, SceneRenderer
    from patrol.scene.world import World
    world = World(cfg)
    r = SceneRenderer(world, RenderOptions(hfov_at_1x_deg=float(
        cfg.get("optics.hfov_at_1x_deg", 60.0))))
    wp = world.waypoints["WP-07"]
    tgt = world.by_id("TGT-01")
    from patrol.scene.optics import PinholeCamera, hfov_at_zoom
    cam = PinholeCamera(1920, 1080, hfov_at_zoom(60.0, 1.0),
                        (wp.x_m, wp.y_m, world.camera_height_m), wp.yaw_deg, 0, 0)
    pan, tilt = cam.aim_offset_deg(tgt.position)

    panes = []
    for z, label in ((1.0, "巡航态  z=1x"), (3.0, "复核态  z=3x")):
        img, meta = r.render(pose_xy_yaw=(wp.x_m, wp.y_m, wp.yaw_deg),
                             pan_deg=pan, tilt_deg=tilt, zoom=z)
        m = next(m for m in meta if m["target_id"] == "TGT-01")
        p = pixel_density(1920, m["target_size_m"], z, m["distance_m"], 60.0)
        x1, y1, x2, y2 = [int(round(v)) for v in m["bbox"]]
        ok = p >= PMIN
        col = (110, 210, 110) if ok else (80, 140, 240)
        cv2.rectangle(img, (x1 - 6, y1 - 6), (x2 + 6, y2 + 6), col, 3)
        for txt, yy, sc in ((label, 60, 1.4),
                            ("p = %.1f px  (%s 120 px 判据)" % (p, "≥" if ok else "<"), 118, 1.0),
                            ("d = %.2f m   实测框宽 %.1f px" % (m["distance_m"], x2 - x1), 168, 0.9)):
            cv2.putText(img, txt, (40, yy), cv2.FONT_HERSHEY_SIMPLEX, sc, (20, 20, 20), 6, cv2.LINE_AA)
            cv2.putText(img, txt, (40, yy), cv2.FONT_HERSHEY_SIMPLEX, sc, col, 2, cv2.LINE_AA)
        panes.append(img)
        print("  z=%.0f  实测 bbox 宽 %.1f px，公式 p=%.1f px" % (z, x2 - x1, p))
    out.mkdir(parents=True, exist_ok=True)
    path = out / "zoom_compare.png"
    cv2.imwrite(str(path), np.hstack([cv2.resize(p_, (960, 540)) for p_ in panes]))
    print("已存", path)


def main() -> int:
    ap = argparse.ArgumentParser(description="虚拟配电室预览")
    ap.add_argument("--config", default=None)
    ap.add_argument("--headless", action="store_true", help="不开窗口，存帧到 --out")
    ap.add_argument("--demo-zoom", action="store_true", help="只出一张变焦对比图")
    ap.add_argument("--out", default="out", help="输出目录")
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    cfg = Config.load(a.config)
    out = Path(a.out)

    if a.demo_zoom:
        demo_zoom(cfg, out)
        return 0

    chassis, ptz, camera, loc = build_drivers(cfg, seed=a.seed)
    problems = selftest(chassis, ptz, camera)
    if problems:
        print("开机自检未通过，拒绝启动：")
        for p in problems:
            print("  ✗", p)
        return 2
    camera.start(int(cfg.get("camera.width")), int(cfg.get("camera.height")), a.fps)

    headless = a.headless or not os.environ.get("DISPLAY")
    if headless:
        out.mkdir(parents=True, exist_ok=True)
    pan = tilt = 0.0
    zoom = 1.0
    hint = "" if headless else "A/D 转 W/S 俯仰 Q/E 变焦 空格 停/走 H 归位 R 安全事件 L 失锁 ESC 退出"
    t_end = time.time() + a.seconds
    n = 0
    try:
        while time.time() < t_end:
            t0 = time.time()
            f = camera.grab()
            img = annotate(f.image, camera.last_targets(), ptz.status().zoom,
                           cfg, chassis, ptz, loc.get_pose(), hint)
            if headless:
                cv2.imwrite(str(out / ("frame_%04d.jpg" % n)), img,
                            [cv2.IMWRITE_JPEG_QUALITY, 88])
            else:
                cv2.imshow("patrol scene", cv2.resize(img, (1280, 720)))
                k = cv2.waitKey(1) & 0xFF
                if k == 27:
                    break
                if k in (ord('a'), ord('A')):
                    pan = min(170.0, pan + 5)
                if k in (ord('d'), ord('D')):
                    pan = max(-170.0, pan - 5)
                if k in (ord('w'), ord('W')):
                    tilt = min(60.0, tilt + 3)
                if k in (ord('s'), ord('S')):
                    tilt = max(-30.0, tilt - 3)
                if k in (ord('q'), ord('Q')):
                    zoom = max(1.0, zoom - 0.25)
                if k in (ord('e'), ord('E')):
                    zoom = min(3.0, zoom + 0.25)
                if k in (ord('h'), ord('H')):
                    pan = tilt = 0.0
                    zoom = 1.0
                if k == ord(' '):
                    (chassis.resume() if chassis.status().state.value != "MOVING"
                     else chassis.pause("VERIFY_REQUEST"))
                if k in (ord('r'), ord('R')):
                    chassis.force_safety_event("OBSTACLE_DETECTED")
                if k in (ord('l'), ord('L')):
                    loc.force_lost(4000)
                ptz.set_pose(pan, tilt, zoom, PTZSpeed.NORMAL)
            n += 1
            time.sleep(max(0.0, 1.0 / a.fps - (time.time() - t0)))
    finally:
        for d in (camera, ptz, chassis, loc):
            d.close()
        if not headless:
            cv2.destroyAllWindows()
    print("共 %d 帧%s" % (n, ("，已存到 %s/" % out) if headless else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
