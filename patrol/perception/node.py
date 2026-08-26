#!/usr/bin/env python3
"""感知节点。ICD §3（IF-1 DetectionEvent）。

    python -m patrol.perception.node

perception 的唯一输出就是 IF-1。巡航态每 100 ms 发一条——**哪怕没有检出，
也要发空 detections 数组**，mission 靠它判断感知进程还活着。复核态在二级
推理完成后补发一条 stage = VERIFY 的报文。

节点不碰执行器（ICD §1.1 的进程职责表里 perception 那一行是"否"），它与
mission 之间不经过网关，因为二者都不产生执行器动作。

一条纪律贯穿全模块：**读数算法只拿图像与检测框。**场景真值只在打分与渲染
时用。合成检测器给出的 source_target_id 仅用于查表拿标定先验（量程、扫过角
——这些在真机上也是标定阶段录入的合法信息），不用于读数本身。
"""
from __future__ import annotations

import argparse
import signal
import sys
import time

import numpy as np

from patrol.common import messages as M
from patrol.common.bus import Publisher, Subscriber
from patrol.common.clock import mono_ns, stamps
from patrol.common.config import Config
from patrol.common.ids import SeqCounter, new_uuid
from patrol.common.logkit import build_logger, set_context
from patrol.drivers.base import ExecProgress, ICamera, IPTZ, selftest
from patrol.drivers.factory import build_drivers
from patrol.perception.anomaly import build_anomaly
from patrol.perception.detector.base import Detection, build_detector
from patrol.perception.detector.synthetic import CLASS_SIZE_M
from patrol.perception.quality import evaluate as eval_quality
from patrol.perception.reading.indicator import read_indicator_light
from patrol.perception.reading.pointer import read_pointer_gauge, reading_to_l2
from patrol.perception.reading.switch import read_switch_position
from patrol.perception.tracker import IouTracker
from patrol.scene.optics import pixel_density

#: 需要 L2 读数的类别，对应 l2_reading.kind
L2_KIND = {
    "PRESSURE_GAUGE": "POINTER_GAUGE", "OIL_LEVEL_GAUGE": "POINTER_GAUGE",
    "INDICATOR_LIGHT": "INDICATOR_LIGHT", "SWITCH_HANDLE": "SWITCH_POSITION",
}
#: severity 查表。ICD 附录 B.1。
SEVERITY = {
    "PRESSURE_GAUGE": 0.70, "INDICATOR_LIGHT": 0.50, "OIL_LEAK": 0.90,
    "OIL_LEVEL_GAUGE": 0.60, "SWITCH_HANDLE": 0.80, "INSULATOR_BREAK": 0.95,
    "RUST_CORROSION": 0.40, "FOREIGN_OBJECT": 0.60, "DOOR_OPEN": 0.50,
    "CABLE_LOOSE": 0.85,
}


class PerceptionNode:
    def __init__(self, cfg: Config, *, seed: int = 0, drivers=None):
        self.cfg = cfg
        self.log = build_logger("perception", cfg)
        self.chassis, self.ptz, self.camera, self.loc = (
            drivers if drivers is not None else build_drivers(cfg, seed=seed))
        problems = selftest(self.chassis, self.ptz, self.camera)
        if problems:
            for p in problems:
                self.log.critical("开机自检未通过", detail=p)
            raise SystemExit("开机自检未通过，感知节点拒绝启动")

        self.fps = float(cfg.get("perception.fps", 10))
        self.hfov1x = float(cfg.get("optics.hfov_at_1x_deg", 60.0))
        self.qcfg = dict(cfg.get("perception.quality", {}))
        self.q_enabled = bool(self.qcfg.get("enabled", True))
        self.q_thr = float(self.qcfg.get("threshold", 0.75))
        self.p_min = float(self.qcfg.get("pixel_density_target", 120.0))
        self.first_release = set(cfg.get("mission.first_release_classes", []))

        self.detector = build_detector(cfg, self.camera)
        self.tracker = IouTracker(
            iou_threshold=float(cfg.get("perception.tracker.iou_threshold", 0.3)),
            max_age=int(cfg.get("perception.tracker.max_age_frames", 15)))
        self.anomaly = build_anomaly(cfg)

        self.pub = Publisher(cfg.get("bus.detection"))
        self.status_sub = Subscriber(cfg.get("bus.status"), topics=["STATUS_REPORT"])
        self.seq = SeqCounter()
        self.run_id = "00000000-000000-0000"
        self.event_id: str | None = None
        self._verified_tracks: set[int] = set()
        self._last_status: dict | None = None
        self._running = False

    # ------------------------------------------------------------ 状态
    def _refresh_status(self) -> dict | None:
        for m in self.status_sub.drain(max_n=64):
            self._last_status = m
            if m.get("run_id"):
                self.run_id = m["run_id"]
        return self._last_status

    def _context(self, frame) -> dict:
        """DetectionEvent.context。云台状态取自 IF-3，位姿取自定位驱动。"""
        st = self._last_status
        ptz = self.ptz.status()
        pose = self.loc.get_pose()
        chassis = self.chassis.status()
        wp = None
        if st is not None:
            wp = st["chassis"].get("current_waypoint_id")
        return {
            "waypoint_id": wp,
            "pose": {"x_m": pose.x_m, "y_m": pose.y_m,
                     "yaw_deg": float(np.clip(pose.yaw_deg, -180, 180)),
                     "cov_trace": max(0.0, pose.cov_trace)},
            "pose_valid": bool(pose.valid),
            "speed_mps": float(np.clip(chassis.speed_mps, 0.0, 1.5)),
            "ptz": {"pan_deg": float(np.clip(ptz.pan_deg, -170, 170)),
                    "tilt_deg": float(np.clip(ptz.tilt_deg, -30, 60)),
                    "zoom": float(np.clip(ptz.zoom, 1.0, 3.0)),
                    "hfov_deg": float(ptz.hfov_deg)},
            "image_w": int(frame.width), "image_h": int(frame.height),
        }

    # ------------------------------------------------------------ 读数
    def _l2_read(self, image, det: Detection, priors: dict | None) -> dict | None:
        kind = L2_KIND.get(det.defect_class)
        if kind is None or priors is None:
            return None
        if kind == "POINTER_GAUGE":
            r = read_pointer_gauge(image, det.bbox, priors)
            l2 = reading_to_l2(r, priors)
            l2["roi"] = [float(max(0.0, v)) for v in det.bbox]
            return l2
        if kind == "INDICATOR_LIGHT":
            r = read_indicator_light(image, det.bbox, priors)
        else:
            r = read_switch_position(image, det.bbox, priors)
        normal = priors.get("normal_states")
        return {"kind": kind, "value": r.value if r.ok else None,
                "unit": None, "range_min": None, "range_max": None,
                "in_normal_band": (None if not r.ok or not normal
                                   else bool(r.value in normal)),
                "reading_confidence": float(np.clip(r.confidence, 0, 1)),
                "roi": [float(max(0.0, v)) for v in det.bbox]}

    def _priors_for(self, det: Detection) -> dict | None:
        """取标定阶段录入的先验。真机上来自标定表，桩上来自场景配置。

        **先验不含当前读数**（World.Target.priors 明确剔除了 value），
        所以拿它不构成偷看真值。
        """
        world = getattr(self.camera, "world", None)
        if world is None or det.source_target_id is None:
            return None
        t = world.by_id(det.source_target_id)
        return t.priors if t is not None else None

    # ------------------------------------------------------------ 一帧
    def process_frame(self, frame, *, stage: str = "CRUISE") -> dict:
        t_cap = frame.ts_mono_ns
        model = self.detector.model_info(stage)
        conf_thr = float(model["conf_threshold"])
        t_infer0 = mono_ns()
        dets = self.detector.infer(frame.image, conf_threshold=conf_thr, stage=stage)
        t_infer1 = mono_ns()
        dets = self.tracker.update(dets)
        ptz = self.ptz.status()
        zoom = float(np.clip(ptz.zoom, 1.0, 3.0))

        out: list[dict] = []
        best_suspect: tuple[float, Detection, str] | None = None
        for d in dets:
            dist = float(d.extra.get("distance_m", 5.0))
            size_m = float(d.extra.get("target_size_m",
                                       CLASS_SIZE_M.get(d.defect_class, 0.15)))
            p = pixel_density(frame.width, size_m, zoom, dist, self.hfov1x)
            priors = self._priors_for(d)
            l2 = self._l2_read(frame.image, d, priors) if stage == "VERIFY" or \
                p >= self.p_min else None

            entry = {
                "track_id": max(0, int(d.track_id)),
                "defect_class": d.defect_class,
                "confidence": float(np.clip(d.confidence, 0, 1)),
                "bbox": [float(max(0.0, v)) for v in d.bbox],
                "target_size_m": size_m,
                "est_distance_m": max(1e-3, dist),
                "pixel_density_px": round(float(p), 3),
                "aim_offset": self._aim_offset(frame, d),
                "l2_reading": l2,
            }
            if self.q_enabled:
                q = eval_quality(frame.image, d.bbox, target_size_m=size_m,
                                 zoom=zoom, distance_m=dist,
                                 hfov_at_1x_deg=self.hfov1x, cfg_quality=self.qcfg)
                entry["quality"] = q.as_dict()
            out.append(entry)

            rule = self._trigger_rule(d, p, l2, entry.get("quality"))
            if rule is not None:
                prio = (SEVERITY.get(d.defect_class, 0.5) * entry["confidence"]
                        * self._novelty(d.track_id))
                if best_suspect is None or prio > best_suspect[0]:
                    best_suspect = (prio, d, rule)

        l3 = self._run_l3(frame, dets)
        if l3 is not None and l3["is_anomaly"] and best_suspect is None and dets:
            d0 = dets[0]
            best_suspect = (SEVERITY.get(d0.defect_class, 0.5) * 0.5, d0, "L3_ANOMALY")

        suspect = self._build_suspect(best_suspect)
        if suspect["is_suspect"] and self.event_id is None:
            self.event_id = new_uuid()
            set_context(event_id=self.event_id)
        if not suspect["is_suspect"] and stage == "CRUISE":
            pass    # event_id 由 mission 在复核结束时清

        t_now = mono_ns()
        latency = {
            "capture_to_infer": max(0, int((t_infer0 - t_cap) // 1_000_000)),
            "infer": max(0, int((t_infer1 - t_infer0) // 1_000_000)),
            "postproc": max(0, int((t_now - t_infer1) // 1_000_000)),
            "total": max(0, int((t_now - t_cap) // 1_000_000)),
        }
        mono, utc = stamps()
        return M.build_detection_event(
            seq=self.seq.next(), ts_mono_ns=t_cap, ts_utc_ms=utc,
            run_id=self.run_id,
            event_id=self.event_id if suspect["is_suspect"] else None,
            stage=stage, model=model, context=self._context(frame),
            detections=out, suspect=suspect, latency_ms=latency, l3_anomaly=l3)

    def _aim_offset(self, frame, d: Detection) -> dict:
        """把目标转到画面中心所需的云台增量。

        由像素偏差换算：Δφ = e · θ_hfov / W。这是**前馈**量，A1 的 PID 伺服
        用它做初值，之后靠像素偏差闭环收敛。
        """
        ptz = self.ptz.status()
        hfov = float(ptz.hfov_deg)
        vfov = hfov * frame.height / max(1, frame.width)
        ex = d.cx - frame.width / 2.0
        ey = d.cy - frame.height / 2.0
        return {"pan_deg": round(-ex * hfov / max(1, frame.width), 4),
                "tilt_deg": round(-ey * vfov / max(1, frame.height), 4)}

    def _trigger_rule(self, d: Detection, p: float, l2, quality) -> str | None:
        """复核触发判据。ICD §3.3 五条 + 差异清单 A4 增补的 QUALITY_LOW。"""
        if d.defect_class not in self.first_release and self.first_release:
            return None
        conf = float(d.confidence)
        # confidence ≥ 0.60 的检出直接判定为缺陷，不占复核预算
        if 0.25 <= conf < 0.60:
            return "CONF_BAND"
        if d.defect_class in L2_KIND and p < self.p_min:
            return "L2_UNREADABLE"
        if l2 is not None and l2.get("in_normal_band") is False:
            return "L2_OUT_OF_BAND"
        if quality is not None and float(quality.get("score", 1.0)) < self.q_thr:
            return "QUALITY_LOW"
        return None

    def _novelty(self, track_id: int) -> float:
        """本轮首次出现取 1.0，此前已复核过取 0.3。

        取 0.3 而不是 0，是为了让复现的缺陷仍有机会被复核，只是排在新发现
        之后。取 0 会导致某个缺陷第一轮复核失败之后永远排不上队。
        """
        return 0.3 if track_id in self._verified_tracks else 1.0

    def _build_suspect(self, best) -> dict:
        if best is None:
            return M.make_suspect(is_suspect=False)
        prio, d, rule = best
        return M.make_suspect(
            is_suspect=True, trigger_rule=rule, target_track_id=max(0, int(d.track_id)),
            severity=SEVERITY.get(d.defect_class, 0.5),
            novelty=self._novelty(d.track_id),
            priority=float(np.clip(prio, 0.0, 1.0)), suppressed_by=None)

    def _run_l3(self, frame, dets) -> dict | None:
        """L3 只喂"看起来正常"的样本学习，对每个检出打分。

        输出只允许进人工复核队列，不得直接告警（ICD §3.1）。
        """
        if self.anomaly is None or not dets:
            return None
        worst = None
        for d in dets:
            res = self.anomaly.score(frame.image, d.bbox)
            if worst is None or res.anomaly_score > worst.anomaly_score:
                worst = res
            if not res.is_anomaly and d.confidence >= 0.6:
                self.anomaly.observe_normal(frame.image, d.bbox)
        return worst.to_dict() if worst is not None else None

    def mark_verified(self, track_id: int) -> None:
        self._verified_tracks.add(int(track_id))

    # ------------------------------------------------------------ 主循环
    def serve_forever(self) -> None:
        self._running = True
        self.camera.start(int(self.cfg.get("camera.width")),
                          int(self.cfg.get("camera.height")), int(self.fps))
        period = 1.0 / max(1e-3, self.fps)
        self.log.info("感知节点启动", fps=self.fps,
                      detector=self.cfg.get("perception.detector"),
                      l3=bool(self.anomaly))
        try:
            while self._running:
                t0 = time.monotonic()
                self._refresh_status()
                frame = self.camera.grab()
                ev = self.process_frame(frame, stage="CRUISE")
                self.pub.send(ev)
                if ev["latency_ms"]["total"] > int(1000 / self.fps):
                    self.log.warn("单帧超出节拍", total_ms=ev["latency_ms"]["total"])
                time.sleep(max(0.0, period - (time.monotonic() - t0)))
        finally:
            self.close()

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self._running = False
        for d in (self.camera, self.ptz, self.chassis, self.loc):
            try:
                d.close()
            except Exception:            # noqa: BLE001
                pass
        self.detector.close()
        self.pub.close()
        self.status_sub.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="感知节点")
    ap.add_argument("--config", default=None)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    node = PerceptionNode(Config.load(a.config), seed=a.seed)
    signal.signal(signal.SIGINT, lambda *_: node.stop())
    signal.signal(signal.SIGTERM, lambda *_: node.stop())
    node.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
