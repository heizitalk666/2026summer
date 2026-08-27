#!/usr/bin/env python3
"""虚拟配电室实时预览。

    python -m patrol.tools.viewer                    # 有显示器：开窗口，键盘控云台
    python -m patrol.tools.viewer --headless --out out/   # 无显示器：存帧
    python -m patrol.tools.viewer --demo-zoom --out out/  # 存一张变焦对比图
    python -m patrol.tools.viewer --live                  # 跟着正在跑的系统看

键位：A/D 左右转，W/S 俯仰，Q/E 变焦，空格 暂停/恢复行驶，
      H 云台归位，R 强制安全事件，L 强制定位失锁，ESC 退出。

画面上每个目标标出 **像素密度 p**：低于 120 px 判据画橙框（读不准，必须
复核），达标画绿框。这一幕就是全项目的立论——巡航态 5 m 处表盘只有 50 px，
3× 变焦后 150 px。

`--live` 是第三个显示面（另外两个是 tools/console.py 的终端流水和云端网页）。
它不自己控云台，而是**跟着正在跑的系统看**：订阅 IF-3 拿到真实位姿与云台角，
经 ICamera.observe_state 回灌给相机桩——这正是感知节点在桩模式下看见东西的
同一条通路（见 docs/架构说明.md"桩模式下物理世界住在哪个进程"）。同时跟读
网关审计日志，把每条下发的指令叠在画面右上角。

于是"指令下去了、云台转了、画面跟着变了"这三件事第一次能在**同一块屏幕上**
对上号——这是没有硬件时最接近"看着车动"的东西。
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
from patrol.tools.textdraw import cjk_available, draw_text, panel, text_size

PMIN = 120.0


def annotate(img, targets, zoom, cfg, chassis, ptz, pose, hint="",
             *, show_bar: bool = True):
    w = img.shape[1]
    hfov1x = float(cfg.get("optics.hfov_at_1x_deg", 60.0))
    for m in targets:
        x1, y1, x2, y2 = [int(round(v)) for v in m["bbox"]]
        p = pixel_density(w, m["target_size_m"], zoom, m["distance_m"], hfov1x)
        ok = p >= PMIN
        col = (110, 210, 110) if ok else (80, 140, 240)
        cv2.rectangle(img, (x1, y1), (x2, y2), col, 2)
        # 用 textdraw 而不是 cv2.putText：后者画不了中文，"达标 / 需复核"
        # 会变成一串方框——而这两个词正是这张图要说的话
        # 标签靠右边缘时会被裁掉半截——把它推回画面内，否则最右边那块表
        # （往往正是刚复核完的那块）的读数信息在图上是缺的
        label = "%s p=%.0fpx d=%.2fm %s" % (
            m["defect_class"], p, m["distance_m"], "达标" if ok else "需复核")
        lw = text_size(label, 20)[0]
        lx = min(max(4, x1), max(4, img.shape[1] - lw - 8))
        draw_text(img, label, (lx, max(2, y1 - 26)), size=20, color=col)

    st, ps = chassis.status(), ptz.status()
    if not show_bar:
        # 跟车模式下本地这套桩是被动的，它自己的 chassis/ptz 一动不动。
        # 照着它画状态栏会得出"车没在走、云台没转"的错觉——真值在 IF-3 里，
        # 由 annotate_live 画。
        return img
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
    y = 16
    panel(img, 8, 8, 780, 30 * len(bar) + 14)
    for line in bar:
        draw_text(img, line, (16, y), size=22, color=(235, 238, 242))
        y += 30
    if st.safety_layer_active:
        draw_text(img, "⚠ 底盘安全层介入", (16, y + 4), size=26,
                  color=(70, 70, 250))
    return img


# ------------------------------------------------------------------ 跟车
#: 指令卡片保留多少条。太少看不出一次复核的完整节奏（暂停→转向→微调→变焦→
#: 复位→恢复），太多会把画面盖住。六条正好装得下一次完整的复核。
LIVE_CARD_ROWS = 6


def annotate_live(img, snap, lines, *, stale_s=None):
    """把"系统正在下发的指令"叠在画面上。

    位置选右上角：左上角已经被状态栏占了，而目标框大多落在画面中部偏左
    （柜列在过道左侧）。右上角是这张图里最空的一块。
    """
    W = img.shape[1]
    # 状态栏用 IF-3 的真值，拆成两行——一行塞不下就会溢出去顶到右边的指令卡片
    left = [
        "车 (%.2f, %.2f) yaw=%+.0f°  %s %.2f m/s  %s"
        % (snap.x_m, snap.y_m, snap.yaw_deg, snap.chassis, snap.speed_mps,
           snap.waypoint_id or "-"),
        "云台 pan=%+.1f° tilt=%+.1f° zoom=%.2f× 视场 %.1f°  电量 %.0f%%"
        % (snap.pan_deg, snap.tilt_deg, snap.zoom, snap.hfov_deg,
           snap.battery_pct),
    ]
    if snap.safety_active:
        left.append("⚠ 安全层已介入")
    bar_w = min(620, int(W * 0.34))
    panel(img, 8, 8, bar_w, 14 + 28 * len(left))
    y = 14
    for line in left:
        draw_text(img, line, (16, y), size=20,
                  color=(70, 70, 250) if line.startswith("⚠") else (235, 238, 242))
        y += 28

    # 指令卡片放右上角：左上角给了状态栏，而目标框大多落在画面中部偏左
    # （柜列在过道左侧），右上角是这张图里最空的一块
    card_w = min(700, int(W * 0.38))
    x0 = W - card_w - 16
    rows = lines[-LIVE_CARD_ROWS:]
    panel(img, x0, 8, card_w, 36 + 26 * max(1, len(rows)))
    head = "指令流水"
    if stale_s is not None and stale_s > 3.0:
        head += "（已 %.0f s 没有新指令）" % stale_s
    draw_text(img, head, (x0 + 12, 12), size=19, color=(200, 205, 212))
    y = 40
    for ln in rows:
        col = (150, 225, 160) if ln.ok else (70, 70, 250)
        draw_text(img, ("%s %s %s" % ("√" if ln.ok else "×", ln.target,
                                      ln.text))[:42],
                  (x0 + 12, y), size=19, color=col)
        y += 26
    return img


def live_loop(cfg, a) -> int:
    """跟着正在跑的系统看。**只读：不建执行器、不发指令。**

    相机桩看见什么，取决于它以为自己在哪、朝哪。跟车模式下这两件事都来自
    IF-3，经 ICamera.observe_state 回灌——和感知节点走的是同一条通路。
    不这么做的话，这个进程里会多出一台幽灵车，画面和真车看到的越差越远
    （这正是端到端一直跑不通的那个根因，见 docs/设计思想.md §5）。
    """
    from patrol.common.bus import Subscriber
    from patrol.tools.console import AuditTail, Snapshot, describe

    chassis, ptz, camera, loc = build_drivers(cfg, seed=a.seed, passive=True)
    camera.start(int(cfg.get("camera.width")), int(cfg.get("camera.height")), a.fps)
    sub = Subscriber(cfg.get("bus.status"), topics=["STATUS_REPORT"])
    tail = AuditTail(cfg.get("gateway.audit_log", "logs/gateway-audit.jsonl"))
    snap, lines = Snapshot(), []
    headless = a.headless or not os.environ.get("DISPLAY")
    out = Path(a.out)
    if headless:
        out.mkdir(parents=True, exist_ok=True)
    if not cjk_available():
        print("提示：没找到中文字体，叠加层将退回英文缩写")
    print("跟车模式：订阅 %s，跟读 %s" % (cfg.get("bus.status"), tail.path))

    t_end = time.time() + a.seconds
    n = 0
    last_cmd_s = None
    try:
        while time.time() < t_end:
            t0 = time.time()
            for st in sub.drain(max_n=200):
                try:
                    snap.update_status(st)
                except (TypeError, ValueError, KeyError):
                    continue
            new = [describe(r) for r in tail.poll()
                   if r.get("command") != "HEARTBEAT"]
            if new:
                lines = (lines + new)[-40:]
                last_cmd_s = time.time()
            camera.observe_state(pose_xy_yaw=(snap.x_m, snap.y_m, snap.yaw_deg),
                                 pan_deg=snap.pan_deg, tilt_deg=snap.tilt_deg,
                                 zoom=snap.zoom, speed_mps=snap.speed_mps)
            f = camera.grab()
            img = annotate(f.image, camera.last_targets(), snap.zoom, cfg,
                           chassis, ptz, loc.get_pose(), show_bar=False)
            img = annotate_live(
                img, snap, lines,
                stale_s=None if last_cmd_s is None else time.time() - last_cmd_s)
            if headless:
                cv2.imwrite(str(out / ("live_%04d.jpg" % n)), img,
                            [cv2.IMWRITE_JPEG_QUALITY, 88])
            else:
                cv2.imshow("patrol live", cv2.resize(img, (1280, 720)))
                if (cv2.waitKey(1) & 0xFF) == 27:
                    break
            n += 1
            time.sleep(max(0.0, 1.0 / a.fps - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        sub.close()
        tail.close()
        for d in (camera, ptz, chassis, loc):
            d.close()
        if not headless:
            cv2.destroyAllWindows()
    print("共 %d 帧%s" % (n, ("，已存到 %s/" % out) if headless else ""))
    return 0


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
    ap.add_argument("--live", action="store_true",
                    help="跟着正在跑的系统看：订阅 IF-3、叠加下发的指令，不自己控云台")
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
    if a.live:
        return live_loop(cfg, a)

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
