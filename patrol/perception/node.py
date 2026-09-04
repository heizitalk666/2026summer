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
import json
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
from patrol.perception.fusion import Evidence, fuse
from patrol.perception.ocr.base import build_ocr
from patrol.perception.quality import evaluate as eval_quality
from patrol.perception.segment.base import build_segmenter
from patrol.perception.reading.indicator import read_indicator_light
from patrol.perception.reading.pointer import read_pointer_gauge, reading_to_l2
from patrol.perception.reading.scale import wrap180
from patrol.perception.reading.nameplate import (cross_check_dial,
                                                 parse_dial_text,
                                                 read_digital_value,
                                                 read_switch_text)
from patrol.perception.reading.switch import read_switch_position
from patrol.perception.tracker import IouTracker
from patrol.scene.optics import (distance_from_bbox_height, pixel_density,
                                 vfov_from_hfov)

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
        # 第四个模型：OCR。它只在复核期跑（见 ocr/rapid.py 的耗时说明），
        # 装不上时 build_ocr 返回 DisabledOcr，互证通路降级但链路不断。
        self.ocr = build_ocr(cfg)
        # 分割：读数的第二种实现。默认 builtin（=None），读数走几何法——
        # 这是 bench_models 实测定下来的默认，见 configs/system.yaml 的说明。
        try:
            self.segmenter = build_segmenter(cfg)
        except (FileNotFoundError, ValueError) as e:
            self.log.warn("分割模型不可用，读数退回几何法", detail=str(e))
            self.segmenter = None
        if self.segmenter is not None:
            self.log.info("分割级联已启用", **self.segmenter.model_info())
        if not self.ocr.available:
            self.log.warn("OCR 互证通路未启用",
                          detail=str(self.ocr.model_info().get("reason", "")))
        else:
            self.log.info("OCR 互证通路就绪", **{
                k: v for k, v in self.ocr.model_info().items()
                if k in ("name", "backend", "offline")})

        self.pub = Publisher(cfg.get("bus.detection"))
        self.status_sub = Subscriber(cfg.get("bus.status"), topics=["STATUS_REPORT"])
        self.seq = SeqCounter()
        self.run_id = "00000000-000000-0000"
        self.event_id: str | None = None
        self._event_started_ns = 0
        #: 触发这次复核时（巡航期）的检测置信度。融合层要拿它算
        #: "复核有没有把置信度打下去"，而复核期这一帧只看得到复核后的值。
        self._event_conf_before = 0.0
        #: 这次复核状态机会把变焦拉到多少倍，用它判断 ZOOM 走完了没有。
        #: 见 verify_due() / _zoom_settled()。
        self._event_target_zoom = 0.0
        #: "只差倍率没到位"这个状态是从什么时候开始连续保持的。见 verify_due()
        #: 末尾的兜底。0 表示当前不处于这个状态。
        self._verify_ready_since_ns = 0
        self.verify_grace_s = float(
            cfg.get("perception.verify_grace_s", 1.5))
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
        # 下面这两个（连同 self.capture_mode）**目前没有任何地方读**——A3 的条件式
        # 辅视角只有配置项与 Schema 字段，没有代码路径，见 mission/fsm.py
        # 的 _st_capture() 说明。留着是为了实现那天不用再改配置结构。
        self.highlight_trigger = float(cap.get("highlight_trigger", 0.12))
        self.cruise_zoom = float(cfg.get("mission.cruise_ptz.zoom", 1.0))
        self.max_zoom = float(cfg.get("optics.max_zoom", 3.0))
        # 巡航姿态：云台盯着柜列。车沿过道往返，所以指向有两个（±bearing）。
        # verify_due() 拿它判断"云台有没有离开巡航姿态"，见那里的说明。
        _bearing = float(cfg.get("mission.cruise_ptz.look_map_bearing_deg", 90.0))
        self.cruise_pans = (_bearing, -_bearing)
        self.cruise_tilt = float(cfg.get("mission.cruise_ptz.tilt_deg", 2.0))
        self.cruise_pose_tol_deg = float(
            cfg.get("mission.cruise_ptz.retarget_deg", 12.0))
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
            r = read_pointer_gauge(image, det.bbox, priors,
                                   segmenter=self.segmenter)
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

    def _expected_zoom(self, best_suspect, entries: list[dict],
                       cur_zoom: float) -> float:
        """这次复核状态机会把变焦拉到多少倍。

        用的是和 `mission/fsm.py` 完全相同的一行：

            zoom_for_density(当前倍率, 触发时的像素密度, p_target, max_zoom)

        而且 `p` 取的就是 perception 自己刚写进 IF-1 的那个
        `pixel_density_px`——状态机读到的也是这一个数，所以两边算出来逐位
        相同。**这一点是必须的**：perception 靠这个值判断 ZOOM 走完没有，
        差一点就会早触发一拍，而早触发一拍就等于把整次复核废掉
        （verify_due 一命中就立刻发报文并清掉 event_id）。
        """
        from patrol.scene.optics import zoom_for_density
        keep = float(getattr(self, "_event_target_zoom", 0.0) or 0.0)
        if best_suspect is None:
            return keep
        tid = max(0, int(best_suspect[1].track_id))
        p = next((float(e["pixel_density_px"]) for e in entries
                  if int(e["track_id"]) == tid), 0.0)
        if p <= 0.0:
            # **查不到这条 track 就保留上一次的值，不要顶到 max_zoom。**
            # zoom_for_density 对 p<=0 的约定是返回最大倍率，那个约定对"该放多大"
            # 是合理的，对"状态机会放到多大"却是灾难：期望值一旦被顶到 3.0，
            # 而状态机实际只下发 2.14，_zoom_settled 就永远不成立——复核干等到
            # 超时，全程没有任何报错。实测一轮 3 个证据包里废掉一个。
            return keep
        return float(zoom_for_density(float(cur_zoom), p, self.p_min,
                                      self.max_zoom))

    # ------------------------------------------------------------ OCR 互证
    def _l2_ocr(self, image, det: Detection, l2: dict | None,
                priors: dict | None) -> dict | None:
        """第二条读数通路：读表面印着的字，和标定先验对质。

        **只在复核期跑。**巡航期 30 Hz 的预算里塞不下 0.3 s 的 OCR，也没有
        意义：5 m 处 1× 时表盘只有约 50 px，上面的刻度数字连轮廓都不成形。
        这条约束和"变焦到 120 px 才谈读数精度"是同一件事的两面——字读不出来
        和针量不准，受制于同一个像素密度。

        返回 None 表示这一路缺席（引擎没装、ROI 太小、一个字都没读到），
        融合层会据此把结论调保守，而不是当作"没问题"。
        """
        if not self.ocr.available or l2 is None:
            return None
        kind = l2.get("kind")
        lines = self.ocr.read(image, det.bbox)
        if not lines:
            return None
        out: dict = {"lines": [{"text": ln.text, "conf": round(ln.conf, 3),
                                "bbox": [round(v, 1) for v in ln.bbox]}
                               for ln in lines]}
        if kind == "POINTER_GAUGE":
            dial = parse_dial_text(lines)
            n_labels = int(self.cfg.get("perception.ocr.dial_labels", 5))
            cross = cross_check_dial(dial, priors, n_labels=n_labels)
            out["dial"] = dial.as_dict()
            out["cross_check"] = cross.as_dict()
            out["_cross"] = cross
        elif kind == "SWITCH_POSITION":
            state, conf = read_switch_text(lines)
            out["state"], out["state_conf"] = state, round(float(conf), 3)
        elif kind == "DIGITAL_DISPLAY":
            value, conf = read_digital_value(lines)
            out["value"], out["value_conf"] = value, round(float(conf), 3)
        return out

    # ------------------------------------------------------------ 融合
    def _fuse(self, entry: dict, ocr: dict | None, l3: dict | None) -> dict:
        """把四路证据交给仲裁层，返回可写盘的结果。

        融合在**感知**这一侧算，因为只有这里同时拿得到四路模型的原始输出：
        IF-1 的 Schema 是 additionalProperties: false，OCR 的原文和互证结论
        塞不进去。结果经证据目录（ICD §6.1"目录即契约"）交给 uploader，
        规则本身两边共用 fusion.fuse()，不会分叉。
        """
        o = ocr or {}
        ev = Evidence(
            defect_class=entry["defect_class"],
            conf_before=float(self._event_conf_before),
            conf_after=float(entry["confidence"]),
            l2=entry.get("l2_reading"),
            cross=o.get("_cross"),
            ocr_state=o.get("state"),
            ocr_value=o.get("value"),
            ocr_conf=float(o.get("state_conf", o.get("value_conf", 0.0)) or 0.0),
            anomaly_score=(None if l3 is None else l3.get("anomaly_score")),
            is_anomaly=bool((l3 or {}).get("is_anomaly")),
            pixel_density_px=float(entry["pixel_density_px"]),
            density_target_px=float(self.p_min),
            quality_score=(entry.get("quality") or {}).get("score"),
            aborted=False)
        return fuse(ev).as_dict()

    def _dump_fusion(self, per_track: dict) -> None:
        """把融合结果写进 <run_id>/<event_id>/fusion.json，供 uploader 合并。

        按 track_id 分桶而不是只写一个"最佳"：状态机是按 track_id 锁定复核
        目标的，uploader 从 mission_ctx.json 拿到的也是 track_id。写死一个
        "最佳"的话，画面里同时有两块表时会挑错那一块——实测过一次复核期间
        两块同类表都在框里的情况。
        """
        if not self.event_id or not per_track:
            return
        d = self.evidence_root / self.run_id / self.event_id
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / "fusion.json").write_text(
                json.dumps({"event_id": self.event_id, "by_track": per_track},
                           ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            self.log.warn("fusion 落盘失败", detail=str(e))

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
        # 复核期才跑 OCR 与融合：巡航期没有预算，也没有可读的字（见 _l2_ocr）
        heavy = (stage == "VERIFY")
        ocr_by_track: dict[int, dict] = {}
        for d in dets:
            size_m = float(d.extra.get("target_size_m",
                                       CLASS_SIZE_M.get(d.defect_class, 0.15)))
            # 距离有两个来源，优先级固定：
            #   1. 检测器直接给 distance_m——**只有合成检测器有**，而且那是
            #      场景真值（加噪 oracle），不是测出来的。写进 est_distance_m
            #      时字段名里的 "est" 对合成路径而言名不副实，报告里要标口径。
            #   2. 由 bbox 高度反算——真机（yolo / 将来的 rknn）走这条。
            # 反算放在这一层而不是检测器里，是因为它**必须代入当前 zoom**：
            # 复核态 3× 时 bbox 高 3 倍，按 1× 反算距离会算成真值的 1/3，
            # 像素密度虚高 3 倍，fusion 那条"密度不达标就不下读数类结论"的
            # 门槛会静默失效。zoom 是云台状态，只有这一层拿得到。
            dist = d.extra.get("distance_m")
            dist = float(dist) if dist is not None else distance_from_bbox_height(
                d.bbox[3] - d.bbox[1], size_m, zoom, frame.width, self.hfov1x)
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
            if heavy:
                o = self._l2_ocr(frame.image, d, l2, priors)
                if o is not None:
                    ocr_by_track[max(0, int(d.track_id))] = o
            out.append(entry)

            rule = self._trigger_rule(d, p, l2, entry.get("quality"))
            if rule is not None:
                prio = (SEVERITY.get(d.defect_class, 0.5) * entry["confidence"]
                        * self._novelty(d.track_id))
                if best_suspect is None or prio > best_suspect[0]:
                    best_suspect = (prio, d, rule)

        l3 = self._run_l3(frame, dets)
        if heavy:
            # 四路证据到齐，交给仲裁层。逐条 track 各算一份——状态机锁的是
            # track_id，写死"最佳"那一个会在画面里有两块同类表时挑错。
            fused = {}
            for e in out:
                tid = int(e["track_id"])
                o = ocr_by_track.get(tid)
                fused[str(tid)] = self._fuse(e, o, l3)
                if o is not None:
                    o.pop("_cross", None)           # CrossCheck 对象不可 JSON 化
                    fused[str(tid)]["evidence"]["l2_ocr"]["raw"] = o
            self._dump_fusion(fused)
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
        # **期望倍率要跟着巡航观测走，不能只在铸主键时记一次。**
        #
        # 复核可以重试：第一次因对焦失败 ABORT 之后 event_id 是保留的
        # （同一个可疑目标，同一个主键），状态机会在车又开近一点之后重来一次。
        # 而它每次都重新算 target_zoom——车近了，需要的倍率就小了。perception
        # 若还攥着第一次那个更大的值去比，第二次永远比不过，VERIFY 干等到超时。
        # 实测：第一次 ABORT 于 t=7.6 s，第二次 t=17.0 s 重试时状态机算的是
        # 2.010×，而 perception 还在等第一次那个更大的数。
        #
        # **只在 cruising 时更新**，也就是"车在走、变焦还在广角端"——这正是
        # 状态机 latch 自己那份 target_zoom 的条件，两边因此对得上。
        #
        # 不能用 `suspect["is_suspect"]` 当条件（试过，栽了）：is_suspect 只在
        # **铸新主键**时才会被降级，一旦主键已经攥在手里，复核全程它都是 true。
        # 于是车停下、云台拉到 2.14× 之后那几十帧照样在刷新期望倍率，而那些帧
        # 里目标时有时无——一帧漏检就把期望值顶到 max_zoom，复核当场死锁。
        if cruising and best_suspect is not None:
            self._event_target_zoom = self._expected_zoom(best_suspect, out, zoom)
        if suspect["is_suspect"] and self.event_id is None:
            if cruising:
                self.event_id = new_uuid()
                self._event_started_ns = mono_ns()
                self._event_conf_before = float(
                    best_suspect[1].confidence if best_suspect else 0.0)
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

        # **持有 event_id 期间，每一条报文都带上它。**ICD §2.2 把它定义成
        # 串起四份 Schema 的主键，那就该覆盖这次事件的整条时间线，而不只是
        # 触发判据恰好成立的那几帧。
        #
        # 只在 is_suspect 为真时带的后果实测过两次：复核态的报文里判据早已
        # 不成立（变焦之后像素密度达标、置信度也上去了），那条报文丢了主键
        # 就配不上 before；而触发前后判据闪断的那几帧不带主键，uploader 会
        # 拿不到 before 快照，增益比算出来是 0——实测一轮里就有一个证据包
        # 只收到了 VERIFY 那一条，before 是空的。
        carry_id = self.event_id

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
        # 视场角**不能**按画幅比例线性缩放。vfov = 2·atan(tan(hfov/2)·H/W)，
        # 640×640 下两者恰好相等所以测不出来，但实际配置是 1920×1080，
        # 线性算法给 33.75° 而真值 35.98°，tilt 前馈会短 6.2 %。
        vfov = vfov_from_hfov(hfov, frame.width, frame.height)
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
        底盘停稳 + 云台离开巡航姿态 + 云台到位 + 对焦锁定。这四条同时成立
        只在复核时发生（巡航态车在动、云台盯着柜列、变焦在广角端）。

        **"离开巡航姿态"不能写成"变焦 ≥ 某个固定值"。**原来这里比的是
        `verify_zoom_min = 1.8`，而状态机下发的变焦是按需算出来的：

            target_zoom = clip(p_target / p_before, 1, max_zoom)

        两个常数是各自独立定的，凑在一起就锁死了：p_target = 120 px 时，
        只有 p_before ≤ 120/1.8 = 66.7 px 的目标才会被放大到 1.8× 以上。
        **巡航期像素密度超过 66.7 px 的目标，复核 100 % 会在 VERIFY 超时**
        ——状态机一路走到 VERIFY 干等，perception 压根不知道该发报文。

        这个失效模式格外恶劣：目标离得越近、看得越清楚，复核越是必然失败，
        而且全程没有任何报错，只在证据包里留下一个 density_ratio = 0 的
        INCONCLUSIVE。实测一轮 7 个证据包里中一个（target_zoom = 1.493）。

        所以判据改成"相对巡航姿态"：变焦抬起来了**或**云台指向偏离了巡航
        指向。两者取或是必要的——目标已经够大时 target_zoom 会等于 1.0，
        变焦这一维根本不动，只剩指向能区分。

        这条仍属于"实现时绕过去了、但接口该补"的情况，已记入差异清单待评审：
        建议 IF-3 的 watchdog 块增补一个可选的 mission_state 字段，由网关
        从心跳里透传，这样 perception 就不用靠状态组合去猜。在那之前，
        上面这个相对判据是冻结接口内能做到的最稳的版本。
        """
        st = self._last_status
        if st is None:
            self._verify_ready_since_ns = 0
            return False
        parked = (st["chassis"]["state"] == "STOPPED"
                  and bool(st["ptz"]["at_target"])
                  and st["ptz"]["focus_state"] == "LOCKED"
                  and self._ptz_left_cruise_pose(st["ptz"]))
        if not parked:
            self._verify_ready_since_ns = 0
            return False
        if self._zoom_settled(st["ptz"]):
            self._verify_ready_since_ns = 0
            return True

        # ---- 兜底 ----
        #
        # 上面四条无歧义的条件全都成立、只有倍率对不上，而且这个状态**稳定
        # 保持**了一段时间——除了"状态机停在 VERIFY 等报文"，系统不会这样干坐着。
        #
        # 这条兜底是被两次事故逼出来的（一次判据偏严、一次偏松，见
        # tests/test_verify_due.py）。它们的共同点不是判据写错，而是**错了之后
        # 没有任何征兆**：状态机干等到超时，证据包留下一个 density_ratio = 0 的
        # INCONCLUSIVE，日志里一行报错都没有。所以这里宁可发一份倍率没完全到位
        # 的复核报文，也要把这件事**变成一条 WARN**——复核质量差一点是可以在
        # 证据包里看出来的，而静默失效看不出来。
        now = mono_ns()
        if self._verify_ready_since_ns == 0:
            self._verify_ready_since_ns = now
            return False
        if (now - self._verify_ready_since_ns) / 1e9 < self.verify_grace_s:
            return False
        self.log.warn("变焦未达期望值就发复核报文（兜底）",
                      want=round(float(self._event_target_zoom), 3),
                      got=round(float(st["ptz"]["zoom"]), 3),
                      held_s=round((now - self._verify_ready_since_ns) / 1e9, 2))
        self._verify_ready_since_ns = 0
        return True

    def _zoom_settled(self, ptz: dict) -> bool:
        """变焦有没有拉到这次复核该到的位置。

        **不能只看"云台离开了巡航姿态"。**AIM 的第一步就是一条
        `PTZ_SET(pan=目标方位, zoom=1.00)` 的前馈粗对准——那一刻底盘停着、
        云台到位、对焦锁定、指向也早就偏离巡航姿态了，四条全中。于是
        verify_due() 会在 AIM 刚结束、ZOOM 还没开始时命中，而它一命中就立刻
        发复核报文并清掉 event_id：整次复核就此作废，证据包里留下一个
        density_ratio ≈ 1.0 的空壳。实测密度比 1.07，等于什么都没复核。

        所以拿期望倍率比：`_event_target_zoom` 是铸主键时用状态机同一个公式
        算出来的。留 5 % 余量是给云台的稳态误差（stub 有 settle_jitter）。
        """
        want = float(getattr(self, "_event_target_zoom", 0.0) or 0.0)
        if want <= 0.0:
            # 没记到期望值（例如 event_id 是别处来的），退回"明显高于巡航端"
            return float(ptz["zoom"]) > self.cruise_zoom * 1.10 + 0.01
        return float(ptz["zoom"]) >= want * 0.95

    def _ptz_left_cruise_pose(self, ptz: dict) -> bool:
        """云台是不是已经离开巡航姿态。

        这一条是给"目标已经够大、状态机算出来根本不用变焦"那种情况兜底的：
        那时 `_zoom_settled` 恒为真（期望倍率就是 1.0），只剩指向能区分
        "在复核"和"车停在路尽头、云台还盯着柜列"。

        容差取 `mission.cruise_ptz.retarget_deg`（默认 12°）：巡航期云台就是
        按这个粒度重新对准柜列的，比它小的偏差属于巡航态的正常抖动。
        """
        if float(ptz["zoom"]) > self.cruise_zoom * 1.10 + 0.01:
            return True
        pan, tilt = float(ptz["pan_deg"]), float(ptz["tilt_deg"])
        # 巡航指向有两个：车头朝东看 +90°、掉头朝西看 -90°，取最近的一个比
        d_pan = min(abs(wrap180(pan - b)) for b in self.cruise_pans)
        d_tilt = abs(tilt - self.cruise_tilt)
        return d_pan > self.cruise_pose_tol_deg or d_tilt > self.cruise_pose_tol_deg

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
        # **闩锁必须跟着主键一起清。**_verify_armed 只在 verify_due() 转 false
        # 的那一支复位；万一它在两次复核之间没被复位，下一次复核就会走进
        # "verify_due 为真但 armed 已置位"的分支——那一支既不发报文也不打日志，
        # 表现为感知在整个 VERIFY 窗口里一声不吭，状态机干等到超时。
        self._verify_armed = False
        self.event_id = None
        self._event_started_ns = 0
        self._event_conf_before = 0.0
        self._event_target_zoom = 0.0
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
