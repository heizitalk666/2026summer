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

def _sharpness(img) -> float:
    """拉普拉斯方差。连拍 3 帧里挑最清晰的一帧送二级模型。

    云台停稳后仍有残余抖动（ptz_stub 的 settle_jitter_deg 就是模拟它），
    3 帧的清晰度确实不同，挑最好的一帧是有意义的，不是摆设。
    """
    import cv2
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return float(cv2.Laplacian(g, cv2.CV_32F).var())


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
        # passive=True：感知不拥有执行器，手里这套桩收不到任何指令。让它自己
        # 跑起来就会在本进程里多出一台幽灵车，位姿与网关那台越差越远。
        self.chassis, self.ptz, self.camera, self.loc = (
            drivers if drivers is not None
            else build_drivers(cfg, seed=seed, passive=True))
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
        self._event_started_ns = 0
        # event_id 的兜底寿命。正常路径由 verify_due() 那一支释放，但 FSM 一旦
        # ABORT，verify_due() 永远不成立，主键就会被盖到之后每一帧上（实测从
        # t=3 s 一直盖到 t=150 s），uploader 把毫不相干的检出粘到同一个证据包，
        # save_cruise_evidence 的去重也把后续所有落盘挡掉。取 25 s：FSM 从
        # HALT_REQ 到 RESUME 的超时之和是 21.5 s，留 3.5 s 余量。
        self.event_id_ttl_s = float(cfg.get("perception.event_id_ttl_s", 25.0))
        self._verified_tracks: set[int] = set()
        self._verify_armed = False
        self._verify_sent_ns = 0
        self._last_saved_event: str | None = None
        cap = cfg.get("mission.capture")
        self.capture_mode = str(cap.get("mode", "conditional")).lower()
        self.burst_n = int(cap.get("burst_n", 3))
        self.burst_interval_ms = int(cap.get("burst_interval_ms", 150))
        self.highlight_trigger = float(cap.get("highlight_trigger", 0.12))
        self.verify_zoom_min = float(cap.get("verify_zoom_min", 1.8))
        self.cruise_zoom = float(cfg.get("mission.cruise_ptz.zoom", 1.0))
        # perception 是唯一持有相机的节点，所以证据图像由它落盘；
        # uploader 订阅 IF-1 配对 before/after 后组装 manifest。
        from pathlib import Path
        self.evidence_root = Path(cfg.get("uploader.evidence_dir", "evidence"))
        self._cruise_ring: list = []
        self._ring_max = int(cap.get("clip_ring_frames", 40))
        self._last_status: dict | None = None
        self._running = False

    # ------------------------------------------------------------ 状态
    def _refresh_status(self) -> dict | None:
        """收 IF-3，顺手把视点喂给相机。

        **这一步是桩上全链路能对上的关键。**四个进程各有一套驱动，只有网关那套
        收得到指令；感知手里的 ptz 永远停在开机位 (0,0,1×)。不把 IF-3 报的位姿
        与云台角回灌给相机桩，感知看到的画面与它自己报的 context 说的就是两个
        世界——实测表现为复核期间 AIM 三十拍一个检出都没有，伺服一个样本都采
        不到。真机上没有这一步，因为真相机的画面本来就来自那台真云台。
        """
        for m in self.status_sub.drain(max_n=64):
            self._last_status = m
            if m.get("run_id"):
                self.run_id = m["run_id"]
        st = self._last_status
        if st is not None:
            p, z = st["pose"], st["ptz"]
            self.camera.observe_state(
                pose_xy_yaw=(float(p["x_m"]), float(p["y_m"]), float(p["yaw_deg"])),
                pan_deg=float(z["pan_deg"]), tilt_deg=float(z["tilt_deg"]),
                zoom=float(z["zoom"]),
                speed_mps=float(st["chassis"].get("speed_mps", 0.0)))
        return st

    def _context(self, frame) -> dict:
        """DetectionEvent.context。

        位姿、车速、云台角**一律以 IF-3 为准**，本地驱动只在还没收到任何 IF-3
        时兜底。mission 拿 context.pose 做 WAYPOINT_ONCE 抑制半径、证据包拿它
        做缺陷定位，报本进程那套收不到指令的桩的读数是错的。
        """
        st = self._last_status
        if st is not None:
            p, z = st["pose"], st["ptz"]
            return {
                "waypoint_id": st["chassis"].get("current_waypoint_id"),
                "pose": {"x_m": float(p["x_m"]), "y_m": float(p["y_m"]),
                         "yaw_deg": float(np.clip(p["yaw_deg"], -180, 180)),
                         "cov_trace": max(0.0, float(p["cov_trace"]))},
                "pose_valid": bool(p.get("valid", True)),
                "speed_mps": float(np.clip(st["chassis"].get("speed_mps", 0.0), 0.0, 1.5)),
                "ptz": {"pan_deg": float(np.clip(z["pan_deg"], -170, 170)),
                        "tilt_deg": float(np.clip(z["tilt_deg"], -30, 60)),
                        "zoom": float(np.clip(z["zoom"], 1.0, 3.0)),
                        "hfov_deg": float(z["hfov_deg"])},
                "image_w": int(frame.width), "image_h": int(frame.height),
            }
        ptz = self.ptz.status()
        pose = self.loc.get_pose()
        chassis = self.chassis.status()
        return {
            "waypoint_id": None,
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
        # context 一帧只算一次：变焦倍率与 hfov 下面还要用，两处必须同源，
        # 否则像素密度与 aim_offset 会按不同的云台状态算，对不上
        ctx = self._context(frame)
        zoom = float(ctx["ptz"]["zoom"])

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
                "aim_offset": self._aim_offset(frame, d, ctx),
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
        # **新起 event_id 有两个前提：车在走，且变焦已经回到巡航端。**
        #
        # 复核全程车是停的、云台是拉到 2.4× 的。这期间（以及 RESUME 之后
        # 变焦退回 1× 的那 800–1400 ms 里）触发判据照样成立，于是会冒出一个
        # "上一次复核的尾巴"事件。它的 before 快照记的是变焦后的样子——实测
        # before.zoom=2.46、p=119 px——像素密度比一算只有 1.01，而复核最核心
        # 的立论就是这个比值应当在 2 以上。更糟的是它会**抢走主键**：
        # perception 同一时刻只持有一个 event_id，真正那条巡航态触发因此拿
        # 不到 id，VERIFY 报文也就配不到它头上，只能等 TTL 变成 INCONCLUSIVE。
        # 实测一轮八个证据包里有三个是这么废掉的。
        st = self._last_status
        cruising = st is None or (
            st["chassis"]["state"] == "MOVING"
            and float(st["ptz"]["zoom"]) <= self.cruise_zoom * 1.10 + 0.01)
        if suspect["is_suspect"] and self.event_id is None:
            if cruising:
                self.event_id = new_uuid()
                self._event_started_ns = mono_ns()
                set_context(event_id=self.event_id)
            else:
                # 拿不到主键就不能报 is_suspect——Schema 规定 is_suspect=true
                # 必须带 event_id（附录 D 的 allOf 条件），这是"每条可疑事件
                # 都可追溯"的强制落点。降级成"被恢复静默抑制"是如实描述：
                # 车刚停完或刚起步、云台还没退回广角，此刻的触发本来就不该
                # 发起新的复核。写进 suppressed_by 让它在 IF-1 里看得见，
                # 而不是悄悄消失（ICD §7.3）。
                suspect = M.make_suspect(
                    is_suspect=False, target_track_id=suspect["target_track_id"],
                    severity=suspect["severity"], novelty=suspect["novelty"],
                    priority=suspect["priority"], suppressed_by="RESUME_SILENCE")

        # **复核全程必须携带同一个 event_id**（ICD §2.2：它是串起四份 Schema
        # 的主键）。复核态的报文里 is_suspect 通常已经变回 false——变焦之后
        # 像素密度达标、二级模型置信度也上去了，触发判据自然不再成立——但
        # 这条报文正是 uploader 用来配对 after 的那一条，丢了 event_id 就
        # 配不上 before，证据包的增益指标全是零。
        carry_id = self.event_id if (suspect["is_suspect"] or stage == "VERIFY") else None

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
            event_id=carry_id, stage=stage, model=model, context=ctx,
            detections=out, suspect=suspect, latency_ms=latency, l3_anomaly=l3)

    def _aim_offset(self, frame, d: Detection, ctx: dict) -> dict:
        """把目标转到画面中心所需的云台增量。

        由像素偏差换算：Δφ = e · θ_hfov / W。这是**前馈**量，A1 的 PID 伺服
        用它做初值，之后靠像素偏差闭环收敛。
        """
        hfov = float(ctx["ptz"]["hfov_deg"])
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

    # ------------------------------------------------------------ 复核时刻
    def verify_due(self) -> bool:
        """判断 mission 是否正在等一条 stage=VERIFY 的报文。

        **这里补的是 ICD 的一个缺口。**§7.2 写着 CAPTURE"走 ICamera"、
        VERIFY"走 perception"，但四条接口里没有任何一条把 mission_state
        送到 perception —— HEARTBEAT.params.mission_state 只从 mission 发往
        gateway，perception 收不到。

        在冻结的接口内可行的判据是看 IF-3 的状态组合：
        底盘停稳 + 变焦拉起来了 + 云台到位 + 对焦锁定。这四条同时成立只在
        复核时发生（巡航态车在动、变焦在广角端），所以用它作为复核时刻的
        判据是可靠的。

        这条属于"实现时绕过去了、但接口该补"的情况，已记入差异清单待评审：
        建议 IF-3 的 watchdog 块增补一个可选的 mission_state 字段，由网关
        从心跳里透传，这样 perception 就不用靠状态组合去猜。
        """
        st = self._last_status
        if st is None:
            return False
        return (st["chassis"]["state"] == "STOPPED"
                and float(st["ptz"]["zoom"]) >= self.verify_zoom_min
                and bool(st["ptz"]["at_target"])
                and st["ptz"]["focus_state"] == "LOCKED")

    def _evidence_dir(self, event_id: str):
        d = self.evidence_root / self.run_id / event_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_cruise_evidence(self, frame, event_id: str, dets: list) -> None:
        """一级检出的原始帧：带框的与不带框的各存一张。

        cruise_raw.jpg 是给重训练用的——标注工具需要干净的原图，带框的图
        没法二次标注。ICD §6.1 把两张都列进目录结构就是这个道理。
        """
        import cv2
        d = self._evidence_dir(event_id)
        q = [cv2.IMWRITE_JPEG_QUALITY, 88]
        cv2.imwrite(str(d / "cruise_raw.jpg"), frame.image, q)
        ann = frame.image.copy()
        for det in dets:
            x1, y1, x2, y2 = [int(round(v)) for v in det["bbox"]]
            cv2.rectangle(ann, (x1, y1), (x2, y2), (80, 140, 240), 2)
            cv2.putText(ann, "%s %.2f p=%.0fpx" % (det["defect_class"],
                        det["confidence"], det["pixel_density_px"]),
                        (x1, max(16, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (80, 140, 240), 1, cv2.LINE_AA)
        cv2.imwrite(str(d / "cruise.jpg"), ann, q)
        if self._cruise_ring:
            self._save_clip(d / "cruise_clip.mp4")

    def _save_clip(self, path) -> None:
        """触发前后的视频片段（差异清单 B3：任务书要求证据含视频）。"""
        import cv2
        frames = list(self._cruise_ring)
        if not frames:
            return
        h, w = frames[0].shape[:2]
        vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             int(self.fps), (w, h))
        if not vw.isOpened():
            return
        for f in frames:
            vw.write(f)
        vw.release()

    def save_verify_evidence(self, frames, event_id: str, roi=None) -> None:
        import cv2
        d = self._evidence_dir(event_id)
        q = [cv2.IMWRITE_JPEG_QUALITY, 88]
        for i, fr in enumerate(frames[:3], start=1):
            cv2.imwrite(str(d / ("verify_%02d.jpg" % i)), fr.image, q)
        if roi is not None:
            cv2.imwrite(str(d / "verify_roi.jpg"), roi, q)

    def run_verify(self) -> dict | None:
        """复核态：连拍 + 二级推理，产出一条 stage=VERIFY 的报文。

        A3 三种采集模式由 mission.capture.mode 决定：
          burst        同一位姿连拍 3 帧（抗云台残余抖动）
          multiview    无条件三视角（抗玻璃反光）
          conditional  默认连拍，高光超阈值时才追加辅视角
        """
        frames = self.camera.grab_burst(self.burst_n, self.burst_interval_ms)
        best = max(frames, key=lambda f: _sharpness(f.image))   # 3 帧挑最清晰的
        ev = self.process_frame(best, stage="VERIFY")
        if ev.get("event_id"):
            roi = None
            dets = ev.get("detections") or []
            if dets:
                x1, y1, x2, y2 = [int(round(v)) for v in dets[0]["bbox"]]
                h, w = best.image.shape[:2]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2 + 1), min(h, y2 + 1)
                if x2 > x1 and y2 > y1:
                    roi = best.image[y1:y2, x1:x2]
            self.save_verify_evidence(frames, ev["event_id"], roi)
        ev["_frames"] = frames
        return ev

    def _clear_event(self) -> None:
        self.event_id = None
        self._event_started_ns = 0
        set_context(event_id=None)

    def _expire_event(self) -> None:
        """超时释放 event_id。见 __init__ 里 event_id_ttl_s 的说明。

        只在底盘 MOVING 时释放：车在动就说明没有复核在进行（复核全程车是停的），
        此时还攥着主键必然是上一次复核中止后没人来收。
        """
        if self.event_id is None or self._event_started_ns == 0:
            return
        st = self._last_status
        if st is not None and st["chassis"]["state"] != "MOVING":
            return
        if (mono_ns() - self._event_started_ns) / 1e9 < self.event_id_ttl_s:
            return
        self.log.warn("event_id 超时释放（上一次复核未走到 VERIFY）",
                      event_id=self.event_id)
        self._clear_event()

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
                self._expire_event()
                if self.verify_due():
                    # 复核时刻只发一次，避免同一次复核被反复触发
                    if not self._verify_armed:
                        self._verify_armed = True
                        ev = self.run_verify()
                        if ev is not None:
                            ev.pop("_frames", None)
                            self.pub.send(ev)
                            self.log.info("已发出复核报文", stage="VERIFY",
                                          event_id=ev.get("event_id"))
                            # 本次复核到此结束，清掉 event_id，下一个可疑目标
                            # 会拿到新的主键
                            if ev.get("suspect", {}).get("target_track_id") is not None:
                                self.mark_verified(ev["suspect"]["target_track_id"])
                            self._clear_event()
                    time.sleep(period)
                    continue
                self._verify_armed = False
                frame = self.camera.grab()
                self._cruise_ring.append(frame.image)
                if len(self._cruise_ring) > self._ring_max:
                    self._cruise_ring.pop(0)
                ev = self.process_frame(frame, stage="CRUISE")
                if ev.get("event_id") and ev["suspect"]["is_suspect"] \
                        and ev["event_id"] != self._last_saved_event:
                    self._last_saved_event = ev["event_id"]
                    self.save_cruise_evidence(frame, ev["event_id"], ev["detections"])
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
