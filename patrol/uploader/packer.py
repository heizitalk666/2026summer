"""证据包打包。ICD §6。

一次复核的完整产物。它同时是三件东西：给云端的告警载荷、答辩时的证据、
算法迭代的数据来源。字段设计以第三点为主，因为前两点用到的字段是第三点
的子集。

目录结构（ICD §6.1）：

    evidence/<run_id>/<event_id>/
    ├── manifest.json     IF-4 报文本体
    ├── cruise.jpg        一级检出的原始帧（含检出框）
    ├── cruise_raw.jpg    同一帧无标注原图，用于重训练
    ├── verify_01..03.jpg 复核抓拍
    ├── verify_aux_l.jpg  A3 条件式辅视角（左偏 15°）
    ├── verify_aux_r.jpg  右偏 15°
    ├── verify_roi.jpg    L2 读数所用 ROI 裁图
    ├── anomaly_heat.png  L3 热力图
    ├── cruise_clip.mp4   触发前后的视频片段（差异清单 B3）
    └── meta.jsonl        复核期间全部 StatusReport 与 ACK 的原始流水

**抓三帧而不是一帧**：云台停稳后仍有残余抖动，3 帧里挑最清晰的一帧送二级
模型，成本是 0.6 s，收益是显著降低运动模糊导致的复核失败。三帧全部入包，
因为丢弃的两帧对分析复核失败原因有用。

**meta.jsonl 是本次复核的完整回放数据。**有了它，一次线上复核失败可以在桩
环境里逐帧重放，不用去现场复现。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from patrol.common import messages as M
from patrol.common.clock import utc_ms

ROLE_BY_PREFIX = {
    "cruise_raw": "CRUISE_RAW", "cruise_clip": "CRUISE_VIDEO", "cruise": "CRUISE_ANNOTATED",
    "verify_roi": "VERIFY_ROI", "verify_aux": "VERIFY_FRAME_AUX", "verify": "VERIFY_FRAME",
    "anomaly": "ANOMALY_HEATMAP", "meta": "META_LOG",
}


def role_of(name: str) -> str:
    for prefix, role in ROLE_BY_PREFIX.items():
        if name.startswith(prefix):
            return role
    return "META_LOG"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class PackResult:
    ok: bool
    directory: Path
    manifest: dict | None = None
    error: str | None = None


class EvidencePacker:
    def __init__(self, cfg):
        self.root = Path(cfg.get("uploader.evidence_dir", "evidence"))
        v = cfg.get("uploader.video", {})
        self.video_enabled = bool(v.get("enabled", True))
        self.video_fps = int(v.get("fps", 10))
        self.jpeg_quality = int(cfg.get("uploader.jpeg_quality", 88))
        ret = cfg.get("uploader.retention", {})
        self.max_days = float(ret.get("max_days", 7))
        self.max_gb = float(ret.get("max_gb", 20.0))

    def dir_for(self, run_id: str, event_id: str) -> Path:
        return self.root / run_id / event_id

    # ------------------------------------------------------------
    def pack(self, ctx, *, run_id: str, verdict: dict,
             meta_lines: list[dict] | None = None,
             annotated: np.ndarray | None = None,
             cruise_raw: np.ndarray | None = None,
             roi: np.ndarray | None = None,
             heatmap: np.ndarray | None = None,
             video_frames: list[np.ndarray] | None = None) -> PackResult:
        """把一次复核落盘成证据包目录。"""
        d = self.dir_for(run_id, ctx.event_id or "unknown")
        try:
            d.mkdir(parents=True, exist_ok=True)
            self._write_images(d, ctx, annotated, cruise_raw, roi, heatmap)
            if self.video_enabled and video_frames:
                self._write_video(d / "cruise_clip.mp4", video_frames)
            if meta_lines:
                with open(d / "meta.jsonl", "w", encoding="utf-8") as f:
                    for line in meta_lines:
                        f.write(json.dumps(line, ensure_ascii=False) + "\n")

            before = ctx.before or _empty_snapshot()
            after = ctx.after or _empty_snapshot()
            gain = M.compute_gain(before, after,
                                  verdict_result=verdict.get("result", "INCONCLUSIVE"),
                                  aborted=ctx.abort is not None)
            files = self._file_list(d)
            if not files:
                return PackResult(False, d, error="证据包为空，至少要有一个文件")
            manifest = M.build_evidence_package(
                run_id=run_id, event_id=ctx.event_id,
                waypoint_id=ctx.waypoint_id or "WP-01", ts_utc_ms=utc_ms(),
                verdict=verdict, before=before, after=after, gain=gain,
                timeline=[t for t in ctx.timeline
                          if t["state"] in ("SUSPECT", "HALT_REQ", "AIM", "ZOOM",
                                            "CAPTURE", "VERIFY", "PACK", "RESUME",
                                            "ABORT")],
                files=files, abort=ctx.abort)
            (d / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            return PackResult(True, d, manifest=manifest)
        except (M.SchemaViolation, OSError, ValueError) as e:
            return PackResult(False, d, error=str(e))

    def _write_images(self, d: Path, ctx, annotated, cruise_raw, roi, heatmap) -> None:
        q = [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
        if annotated is not None:
            cv2.imwrite(str(d / "cruise.jpg"), annotated, q)
        if cruise_raw is not None:
            cv2.imwrite(str(d / "cruise_raw.jpg"), cruise_raw, q)
        for i, fr in enumerate(ctx.frames[:3], start=1):
            img = fr.image if hasattr(fr, "image") else fr
            cv2.imwrite(str(d / ("verify_%02d.jpg" % i)), img, q)
        for name, fr in zip(("verify_aux_l.jpg", "verify_aux_r.jpg"), ctx.aux_frames):
            img = fr.image if hasattr(fr, "image") else fr
            cv2.imwrite(str(d / name), img, q)
        if roi is not None:
            cv2.imwrite(str(d / "verify_roi.jpg"), roi, q)
        if heatmap is not None:
            cv2.imwrite(str(d / "anomaly_heat.png"), heatmap)

    def _write_video(self, path: Path, frames: list[np.ndarray]) -> None:
        """触发前后的视频片段。任务书要求证据含"图像、视频、定位、时间和
        识别结果"，差异清单 B3 指出 ICD 的 files[].role 枚举里漏了视频。"""
        if not frames:
            return
        h, w = frames[0].shape[:2]
        vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             self.video_fps, (w, h))
        if not vw.isOpened():
            return
        for f in frames:
            vw.write(f if f.shape[:2] == (h, w) else cv2.resize(f, (w, h)))
        vw.release()

    def _file_list(self, d: Path) -> list[dict]:
        out = []
        for p in sorted(d.iterdir()):
            if p.name == "manifest.json" or not p.is_file():
                continue
            out.append({"path": p.name, "role": role_of(p.name),
                        "bytes": int(p.stat().st_size),
                        "sha256": sha256_of(p), "uploaded": False})
        return out

    # ------------------------------------------------------------ 保留策略
    def enforce_retention(self) -> dict:
        """本地磁盘保留最近 N 天或 M GB，先到先删；已上传的优先删。

        设计原则是**磁盘满不能导致巡检停止**（方案书 §7.3.3）。
        """
        import time
        if not self.root.exists():
            return {"removed": 0, "freed_mb": 0.0}
        packs = []
        for run_dir in self.root.iterdir():
            if not run_dir.is_dir():
                continue
            for ev in run_dir.iterdir():
                if not ev.is_dir():
                    continue
                mf = ev / "manifest.json"
                size = sum(p.stat().st_size for p in ev.rglob("*") if p.is_file())
                uploaded = False
                if mf.exists():
                    try:
                        m = json.loads(mf.read_text(encoding="utf-8"))
                        uploaded = all(f.get("uploaded") for f in m.get("files", []))
                    except (OSError, ValueError):
                        pass
                packs.append({"dir": ev, "mtime": ev.stat().st_mtime,
                              "size": size, "uploaded": uploaded})
        now = time.time()
        total = sum(p["size"] for p in packs)
        # 已上传的优先删，其次按时间从旧到新
        packs.sort(key=lambda p: (not p["uploaded"], p["mtime"]))
        removed, freed = 0, 0
        for p in packs:
            too_old = (now - p["mtime"]) > self.max_days * 86400
            too_big = total > self.max_gb * (1 << 30)
            if not (too_old or too_big):
                break
            shutil.rmtree(p["dir"], ignore_errors=True)
            total -= p["size"]
            freed += p["size"]
            removed += 1
        return {"removed": removed, "freed_mb": round(freed / (1 << 20), 2)}


def _empty_snapshot() -> dict:
    return M.snapshot(confidence=0.0, pixel_density_px=0.0, zoom=1.0,
                      est_distance_m=1.0, defect_class=None, l2_reading=None)


def decide_verdict(after_l2: dict | None, *, before_conf: float, after_conf: float,
                   defect_class: str | None, is_anomaly: bool,
                   aborted: bool) -> dict:
    """复核结论。ICD §6.3。

    **FALSE_ALARM 是有价值的结论，不是失败。**一级为了保召回把阈值压到
    0.25，必然带来误报，复核把它们消解掉正是这套方案的立论所在。误报被
    复核否掉并记录下来，这条数据回流到训练集，下一轮一级模型在这类背景上
    的误报率就会下降。
    """
    if aborted:
        return {"result": "INCONCLUSIVE", "defect_class": defect_class,
                "severity": "INFO", "needs_human_review": True,
                "confidence": float(np.clip(after_conf, 0, 1))}
    # **有二级读数时以读数为准，L3 排在它后面。**L3 是非监督的，只学过
    # "看起来正常"的样本，对一块读数明确、且落在正常带内的表计报异常，多半
    # 是光照或视角变化引起的重构误差。把它排在读数前面的后果实测过：一整轮
    # 里压力表全被判成 UNKNOWN_ANOMALY，读数通路等于白做。
    # 但 L3 的意见不丢——读数正常而 L3 报异常时置 needs_human_review。
    # 这也符合 ICD §3.1「L3 输出只允许进人工复核队列，不得直接告警」。
    if after_l2 is not None and after_l2.get("value") is not None:
        band = after_l2.get("in_normal_band")
        if band is False:
            return {"result": "READING_ABNORMAL", "defect_class": defect_class,
                    "severity": "WARN", "needs_human_review": False,
                    "confidence": float(np.clip(after_conf, 0, 1))}
        if band is True:
            return {"result": "READING_OK", "defect_class": defect_class,
                    "severity": "INFO", "needs_human_review": bool(is_anomaly),
                    "confidence": float(np.clip(after_conf, 0, 1))}
    if is_anomaly:
        return {"result": "UNKNOWN_ANOMALY", "defect_class": None,
                "severity": "WARN", "needs_human_review": True,
                "confidence": float(np.clip(after_conf, 0, 1))}
    if after_conf >= 0.60:
        return {"result": "CONFIRMED_DEFECT", "defect_class": defect_class,
                "severity": "CRITICAL" if after_conf >= 0.85 else "WARN",
                "needs_human_review": False,
                "confidence": float(np.clip(after_conf, 0, 1))}
    if after_conf < before_conf * 0.6:
        return {"result": "FALSE_ALARM", "defect_class": None, "severity": "INFO",
                "needs_human_review": False,
                "confidence": float(np.clip(after_conf, 0, 1))}
    return {"result": "INCONCLUSIVE", "defect_class": defect_class,
            "severity": "INFO", "needs_human_review": True,
            "confidence": float(np.clip(after_conf, 0, 1))}
