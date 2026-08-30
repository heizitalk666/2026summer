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
from patrol.perception.fusion import Evidence, fuse
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
        """本地磁盘保留最近 N 天或 M GB。**只删已确认上传的包。**

        两条设计原则在这里打架，必须先分清主次：

        - **磁盘满不能导致巡检停止**（方案书 §7.3.3）
        - **未确认的数据永不自动删除**（方案书 §8.3.5）

        原来的实现只做到了第一条：按时间到期就删，不看 uploaded。结果是
        一个连传七天没传上去的证据包会被自己删掉——**恰恰是最该留证的那种，
        因为它没能送到任何别的地方**。现在的规则是：过期或超配额时只删已确认
        的；只剩未确认的还超配额，就如实报出来交给人处理，不自己动手。
        巡检本身不会因此停下——打包失败那条路径状态机早就处理了（PACK_FAILED
        照常转 RESUME）。

        另一个 bug 藏在遍历顺序里：排序是"已上传的优先，其次按时间从旧到新"，
        而循环遇到第一个"既不过期也不超配额"的包就 break。于是只要队头有一个
        新的、已上传的包，后面那些真正过期的就永远轮不到——按时间清理静默失效。
        现在把"要删的"先筛出来再删，不靠 break 提前退出。
        """
        import time
        if not self.root.exists():
            return {"removed": 0, "freed_mb": 0.0, "kept_unconfirmed": 0,
                    "over_quota": False}
        packs = self._scan_packages()
        now = time.time()
        quota = self.max_gb * (1 << 30)
        total = sum(p["size"] for p in packs)

        confirmed = sorted((p for p in packs if p["uploaded"]),
                           key=lambda p: p["mtime"])          # 旧的先删
        unconfirmed = [p for p in packs if not p["uploaded"]]

        removed, freed = 0, 0
        for p in confirmed:
            too_old = (now - p["mtime"]) > self.max_days * 86400
            if not (too_old or total > quota):
                continue                    # 这个不用删，但后面的可能要——不 break
            if not self._remove(p["dir"]):
                continue
            total -= p["size"]
            freed += p["size"]
            removed += 1
        return {"removed": removed, "freed_mb": round(freed / (1 << 20), 2),
                "kept_unconfirmed": len(unconfirmed),
                # 删光了已确认的还超配额，说明剩下的全是没传上去的。
                # 这时候**不删**，报出来让人来看。
                "over_quota": total > quota}

    def _scan_packages(self) -> list[dict]:
        out = []
        for run_dir in self.root.iterdir():
            if not run_dir.is_dir():
                continue
            for ev in run_dir.iterdir():
                if not ev.is_dir():
                    continue
                try:
                    size = sum(p.stat().st_size for p in ev.rglob("*") if p.is_file())
                    mtime = ev.stat().st_mtime
                except OSError:
                    continue
                out.append({"dir": ev, "mtime": mtime, "size": size,
                            "uploaded": self._all_uploaded(ev / "manifest.json")})
        return out

    @staticmethod
    def _all_uploaded(manifest_path: Path) -> bool:
        """没有 manifest、读不动、或者还有文件没确认，一律算**未确认**。

        判错方向的代价不对称：错判成已确认会把没送出去的证据删掉，
        错判成未确认只是多占一点盘。
        """
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        files = m.get("files", [])
        return bool(files) and all(f.get("uploaded") for f in files)

    @staticmethod
    def _remove(d: Path) -> bool:
        """删一个包目录。真删掉了才返回 True——统计数字不能虚报。"""
        shutil.rmtree(d, ignore_errors=True)
        return not d.exists()


def _empty_snapshot() -> dict:
    return M.snapshot(confidence=0.0, pixel_density_px=0.0, zoom=1.0,
                      est_distance_m=1.0, defect_class=None, l2_reading=None)


def decide_verdict(after_l2: dict | None, *, before_conf: float, after_conf: float,
                   defect_class: str | None, is_anomaly: bool, aborted: bool,
                   fusion: dict | None = None) -> dict:
    """复核结论。ICD §6.3。

    **仲裁规则只有一份，住在 patrol/perception/fusion.py。**这里是它的薄封装：
    打包器只拿得到证据包快照里的那几个字段，而感知在复核当时拿得到全部四路
    证据（含 OCR 互证）。所以：

    - 感知随 IF-1 把融合结果带过来时（`fusion` 非空），**直接用它**——它是
      在证据最全的时刻算的
    - 拿不到时（旧数据回放、感知没开 OCR），用手头这几个字段现算一遍。同一个
      `fuse()`，同一套规则，只是证据少一路，结论自然更保守

    两条路共用一份规则，是为了避免"云端看到的结论"和"车上算出的结论"哪天
    悄悄分叉——那种不一致查起来极其痛苦，而且没有任何征兆。
    """
    if fusion:
        keep = ("result", "defect_class", "severity", "needs_human_review",
                "confidence")
        v = {k: fusion[k] for k in keep if k in fusion}
        if set(v) == set(keep):
            v["confidence"] = float(np.clip(v["confidence"], 0.0, 1.0))
            return v
    return fuse(Evidence(
        defect_class=defect_class,
        conf_before=float(before_conf), conf_after=float(after_conf),
        l2=after_l2, is_anomaly=bool(is_anomaly), aborted=bool(aborted),
    )).verdict()
