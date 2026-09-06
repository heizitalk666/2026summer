"""巡航视点下 YOLO 检出稳定性探针。

模拟车沿过道巡航（y=-3.18，云台 pan=90° 对北墙，zoom=1×），每隔 0.5 m 渲染一帧，
跑训练好的 yolo11s，统计每个真值目标的检出率 / 置信度 / 框高（1920 空间），
并对比 imgsz=640 与 1280。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from patrol.common.config import Config
from patrol.scene.world import World
from patrol.scene.render import RenderOptions, SceneRenderer
from patrol.perception.detector.synthetic import CLASS_SIZE_M

cfg = Config.load()
world = World(cfg)

opts = RenderOptions(
    width=1920, height=1080, hfov_at_1x_deg=60.0,
    simulate_4k_crop=True, source_width=3840)

from ultralytics import YOLO
model = YOLO("training/runs/cruise_ft/weights/best.pt")

CLASSES = ["PRESSURE_GAUGE", "INDICATOR_LIGHT", "SWITCH_HANDLE"]


def run_sweep(imgsz: int):
    # 每个imgsz用新renderer，保证噪声序列一致
    rnd = SceneRenderer(world, opts, seed=7)
    stats = {}   # target_id -> dict(det=0, n=0, confs=[], hs=[])
    for i in range(0, 25):
        x = 3.0 + i * 0.5
        img, meta = rnd.render(pose_xy_yaw=(x, -3.18, 0.0), pan_deg=90.0,
                               tilt_deg=2.0, zoom=1.0, speed_mps=0.25)
        if i == 15:   # x=10.5，TGT-02 (10.20) 附近，存图 + 打印原始输出
            import cv2
            cv2.imwrite("_probe_frame.jpg", img)
            print("sample frame at x=%.1f, meta:" % x)
            for m in meta:
                print("   truth: %s bbox=%s dist=%.2f" %
                      (m["defect_class"],
                       [round(float(v), 1) for v in m["bbox"]],
                       m.get("distance_m", -1)))
            raw = model.predict(img, conf=0.05, iou=0.45, imgsz=640,
                                device=0, verbose=False)[0]
            print("   raw dets (conf>=0.05): %d" % len(raw.boxes))
            for b in raw.boxes:
                print("     %s conf=%.3f xyxy=%s" %
                      (raw.names.get(int(b.cls[0]), "?"), float(b.conf[0]),
                       [round(float(v)) for v in b.xyxy[0]]))
        res = model.predict(img, conf=0.25, iou=0.45, imgsz=imgsz,
                            device=0, verbose=False)[0]
        names = res.names
        dets = []
        for b in res.boxes:
            cls = names.get(int(b.cls[0]), "")
            if cls in CLASSES:
                dets.append((cls, float(b.conf[0]),
                             [float(v) for v in b.xyxy[0]]))
        # 真值目标匹配：中心取**当前帧** GT 框（相机在动，目标屏幕位置
        # 随车位移平移，首帧中心会过期导致全错配），框距 < 120px 算同一目标
        for m in meta:
            tid = m["target_id"]
            if m["defect_class"] not in CLASSES:
                continue
            st = stats.setdefault(tid, dict(
                cls=m["defect_class"], det=0, n=0, confs=[], hs=[]))
            st["n"] += 1
            mx = float((m["bbox"][0] + m["bbox"][2]) / 2)
            my = float((m["bbox"][1] + m["bbox"][3]) / 2)
            best, bd = None, 1e9
            for cls, cf, bb in dets:
                cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
                d = ((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5
                if d < bd:
                    bd, best = d, (cls, cf, bb)
            if best is not None and bd < 120:
                st["det"] += 1
                st["confs"].append(best[1])
                st["hs"].append(best[2][3] - best[2][1])
    return stats


for imgsz in (640, 1280):
    stats = run_sweep(imgsz)
    print("\n===== imgsz=%d =====" % imgsz)
    print("%-8s %-16s %5s %6s %7s %7s %8s" %
          ("tid", "class", "seen", "rate", "conf_med", "h_px_med", "conf_min"))
    for tid in sorted(stats):
        st = stats[tid]
        confs = sorted(st["confs"])
        hs = sorted(st["hs"])
        conf_med = "%.3f" % np.median(confs) if confs else "-"
        h_med = "%.0f" % np.median(hs) if hs else "-"
        conf_min = "%.3f" % confs[0] if confs else "-"
        print("%-8s %-16s %5d %5.0f%% %7s %7s %8s" %
              (tid, st["cls"], st["n"], 100.0 * st["det"] / max(1, st["n"]),
               conf_med, h_med, conf_min))
