#!/usr/bin/env python3
"""上传节点。ICD §1.1 / §6。

    python -m patrol.uploader.node

职责：证据包落盘、断点续传、云端上报。它不碰执行器，也不参与决策。

**它靠订阅 IF-1 把 before 与 after 配对起来。**同一个 event_id 上，
stage=CRUISE 的那条报文是 before，stage=VERIFY 的那条是 after，两者结构
相同，直接做差就是复核增益。图像由 perception 落盘（它是唯一持有相机的
节点），uploader 只负责组装 manifest 与上传。

meta.jsonl 存本次复核期间的全部 StatusReport 与 ACK 原始流水——有了它，
一次线上复核失败可以在桩环境里逐帧重放，不用去现场复现。
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

from patrol.common.bus import Subscriber
from patrol.common.clock import mono_ns
from patrol.common.config import Config
from patrol.common.logkit import build_logger
from patrol.uploader.packer import EvidencePacker, decide_verdict
from patrol.uploader.transport import UploadQueue


class _Pending:
    __slots__ = ("event_id", "before", "after", "waypoint_id", "meta",
                 "timeline", "abort", "t0_ns", "defect_class", "alerted")

    def __init__(self, event_id: str):
        self.event_id = event_id
        self.before = None
        self.after = None
        self.waypoint_id = None
        self.defect_class = None
        #: 「发现即报」只发一次。IF-1 是 10 Hz 的，同一个 suspect 会连着出现
        #: 几十帧；不去重的话云端会被同一件事刷屏，真正的新情况反而被淹掉。
        self.alerted = False
        self.meta: list[dict] = []
        self.timeline: list[dict] = []
        self.abort = None
        self.t0_ns = mono_ns()


class _Ctx:
    """喂给 EvidencePacker 的最小上下文（与 MissionFSM.VerifyContext 同形）。"""

    def __init__(self, p: _Pending):
        self.event_id = p.event_id
        self.waypoint_id = p.waypoint_id
        self.before = p.before
        self.after = p.after
        self.timeline = p.timeline
        self.abort = p.abort
        self.frames: list = []
        self.aux_frames: list = []


class UploaderNode:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = build_logger("uploader", cfg)
        self.packer = EvidencePacker(cfg)
        self.queue = UploadQueue(cfg)
        self.det_sub = Subscriber(cfg.get("bus.detection"), topics=["DETECTION_EVENT"])
        self.status_sub = Subscriber(cfg.get("bus.status"), topics=["STATUS_REPORT"])
        self.pending: dict[str, _Pending] = {}
        self.run_id = "00000000-000000-0000"
        self.packed = 0
        self._running = False
        self._last_upload = 0.0
        self.upload_period_s = float(cfg.get("uploader.upload_period_s", 3.0))
        self.stale_s = float(cfg.get("uploader.pending_ttl_s", 60.0))

    # ------------------------------------------------------------
    def on_detection(self, ev: dict) -> None:
        eid = ev.get("event_id")
        if not eid:
            return
        self.run_id = ev.get("run_id", self.run_id)
        p = self.pending.setdefault(eid, _Pending(eid))
        dets = ev.get("detections") or []
        det = dets[0] if dets else None
        tid = (ev.get("suspect") or {}).get("target_track_id")
        if tid is not None:
            det = next((d for d in dets if d.get("track_id") == tid), det)
        # 变焦之后跟踪常常重新分配 id，按 id 找不到就退回**同类别**的第一个，
        # 而不是 detections[0]。否则会出现 before 是开关把手、after 是压力表
        # 的证据包——两张快照说的根本不是同一个东西，做差得到的增益没有意义。
        if p.defect_class and (det is None or det.get("defect_class") != p.defect_class):
            det = next((d for d in dets if d.get("defect_class") == p.defect_class), det)
        if det is None:
            # **复核帧没检出目标也要收尾，不能就这么丢掉。**
            #
            # 这正是方案书 §9.4 点名的失效模式「变焦后目标丢失」：3× 时视场
            # 只有 21.8°，云台残余抖动或位姿稍有偏差，目标就出框了。原来这里
            # 直接 return，于是手上那个 pending 一路等到 TTL 超时，最后落成一个
            # STATE_TIMEOUT——**看起来像状态机卡住了，实际是复核看了但没看见**。
            # 这两件事的处置完全不同（前者查流水线，后者查观测条件），混成同一
            # 个结论等于把信息丢了，也违背"宁可暴露，不要静默通过"。
            #
            # 实测：中等负载下一轮里 uploader 收到 10 条 stage=VERIFY 报文，
            # **全部检出为 0、全部被丢弃**，六次完整复核循环无一落成证据包。
            #
            # 改成照常收尾：after 留空，decide_verdict 拿到 after_conf=0 会给出
            # 需要人工复核的结论，证据包里因此看得出"复核到位了、只是没找到"。
            # 只在确实有巡航基准（before）时才收尾，避免凭空造出证据包。
            if ev.get("stage") == "VERIFY" and p.before is not None:
                self.log.warn("复核帧未检出目标，按复核失败收尾（非超时）",
                              event_id=str(eid)[:8], defect_class=p.defect_class)
                self._finish(p, l3=ev.get("l3_anomaly"))
            return
        snap = {"confidence": float(det["confidence"]),
                "pixel_density_px": float(det["pixel_density_px"]),
                "zoom": float(ev["context"]["ptz"]["zoom"]),
                "est_distance_m": float(det["est_distance_m"]),
                "defect_class": det.get("defect_class"),
                "l2_reading": det.get("l2_reading")}
        p.defect_class = det.get("defect_class")
        p.waypoint_id = (ev.get("context") or {}).get("waypoint_id") or p.waypoint_id

        # ---- 「发现即报」：suspect 一确认就先甩一条轻量告警给云端 ----
        # 证据包要等整个复核周期走完（FSM 预算加总 9.2 s，最坏超时 22 s）才
        # 存在。等它才上报，就成了"发现后 9 秒才报"。这条旁路让云端**秒级**
        # 知道"这里有情况"，完整证据随后补齐。
        #
        # 只带够用的字段，不带图片：告警要小、要快、要能在断网恢复的一瞬间挤
        # 出去。权威记录始终是证据包，告警丢了不丢信息。
        if tid is not None and not p.alerted:
            p.alerted = True
            self._send_alert(ev, det, p)
        if len(p.meta) < 8000:
            # IF-1 也要进 meta.jsonl。只记 IF-3 的话，"一次线上复核失败可以在桩
            # 环境里逐帧重放"这句就落空了——重放要的正是当时的检出框与读数。
            p.meta.append(ev)
        if ev.get("stage") == "VERIFY":
            p.after = snap
            self._finish(p, l3=ev.get("l3_anomaly"))

        elif p.before is None and ev.get("stage") == "CRUISE":
            # before 必须是**巡航态广角端**的那一帧。ICD §6.4 的三项增益
            # （Δconf、像素密度比、复核成功率）全是拿它做基准算的。
            p.before = snap

    def _send_alert(self, ev: dict, det: dict, p: _Pending) -> None:
        """把"这里有情况"秒级送到云端。**失败不重试、不落盘。**

        字段按"够人做决定，不够就去翻证据包"来挑：类别、置信度、像素密度、
        触发判据、航点与位姿。**不带图片**——一张巡航帧 200 KB 上下，断网
        恢复的那一瞬间先该挤出去的是"哪里有情况"，不是那张图。

        整个方法包在 try 里：这是旁路，它出任何问题都不该影响证据包这条
        主路。**告警可以丢，证据不能丢**，这个优先级要在代码里看得出来。
        """
        try:
            sus = ev.get("suspect") or {}
            ctx = ev.get("context") or {}
            pose = ctx.get("pose") or {}
            alert = {
                "run_id": ev.get("run_id"),
                "event_id": p.event_id,
                "ts_utc_ms": int(ev.get("ts_utc_ms") or 0),
                "stage": ev.get("stage"),
                "defect_class": det.get("defect_class"),
                "confidence": round(float(det.get("confidence") or 0.0), 4),
                "pixel_density_px": round(float(det.get("pixel_density_px") or 0.0), 1),
                "est_distance_m": round(float(det.get("est_distance_m") or 0.0), 3),
                "trigger_rule": sus.get("trigger_rule"),
                "severity": sus.get("severity"),
                "waypoint_id": ctx.get("waypoint_id"),
                "x_m": pose.get("x_m"),
                "y_m": pose.get("y_m"),
            }
            # 用 getattr 取而不是直接调：transport 是可替换的（测试替身、
            # 将来的 MQTT/自定义后端都可能没实现这条旁路）。**不支持就跳过**，
            # 而不是抛——旁路的缺失不该变成主路的异常。
            send = getattr(self.queue.transport, "send_alert", None)
            if not callable(send):
                return
            ok = bool(send(alert))
            self.log.info("发现即报", event_id=p.event_id[:8],
                          defect_class=alert["defect_class"],
                          trigger_rule=alert["trigger_rule"], delivered=ok)
        except Exception as e:                              # noqa: BLE001
            # 旁路的异常绝不能掀翻主路。降级成一条 WARN，证据包照常走。
            self.log.warn("告警发送异常，已忽略（证据包不受影响）",
                          event_id=p.event_id[:8], detail=str(e)[:120])

    def on_status(self, st: dict) -> None:
        """复核期间的状态流水全部记进 meta.jsonl，供事后逐帧重放。"""
        for p in self.pending.values():
            if len(p.meta) < 4000:
                p.meta.append(st)
        if st.get("report_kind") == "SAFETY_EVENT":
            sev = (st.get("safety") or {}).get("severity")
            if sev == "CRITICAL":
                for p in self.pending.values():
                    if p.abort is None:
                        p.abort = {"at_state": "VERIFY", "reason": "SAFETY_EVENT",
                                   "detail": str((st.get("safety") or {}).get("detail", ""))[:256]}

    # ------------------------------------------------------------
    def _merge_mission_ctx(self, p: _Pending) -> None:
        """把 mission 落在证据目录里的 FSM 过程合并进来。

        中止原因与各状态耗时只有状态机知道，uploader 只订阅 IF-1/IF-3 看不到。
        ICD §6.1 把证据目录的结构定义成契约，两个进程读写同一个
        <run_id>/<event_id>/ 目录属于这个契约内的用法，不必新开第五条接口。
        没有这个文件（例如 mission 提前退出）就保留 uploader 自己的判断。
        """
        f = Path(self.packer.root) / self.run_id / p.event_id / "mission_ctx.json"
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if m.get("abort"):
            p.abort = m["abort"]        # FSM 的中止才是真的，覆盖掉 TTL 那条
        if m.get("timeline"):
            p.timeline = m["timeline"]
        p.waypoint_id = m.get("waypoint_id") or p.waypoint_id
        p.defect_class = m.get("defect_class") or p.defect_class

    def _merge_fusion(self, p: _Pending) -> dict | None:
        """取感知在复核当时算好的融合结论。

        融合必须在感知那一侧算——四路证据里 OCR 的原文与互证结论进不了 IF-1
        （Schema 是 additionalProperties: false）。所以走的还是证据目录这条
        契约（ICD §6.1），和 mission_ctx.json 同一个办法。

        **按 track_id 取，不取"第一个"。**复核目标是状态机按 track_id 锁定的，
        mission_ctx.json 里记着它；画面里同时有两块同类表时，取错 track 就会
        把另一块表的结论写进这个证据包。取不到就返回 None，由 decide_verdict
        用手头字段现算一份更保守的。
        """
        d = Path(self.packer.root) / self.run_id / p.event_id
        try:
            f = json.loads((d / "fusion.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        by_track = f.get("by_track") or {}
        if not by_track:
            return None
        tid = None
        try:
            tid = json.loads(
                (d / "mission_ctx.json").read_text(encoding="utf-8")).get("track_id")
        except (OSError, ValueError):
            pass
        if tid is not None and str(tid) in by_track:
            return by_track[str(tid)]
        if len(by_track) == 1:
            return next(iter(by_track.values()))
        return None

    def _finish(self, p: _Pending, *, l3: dict | None = None) -> None:
        self._merge_mission_ctx(p)
        fusion = self._merge_fusion(p)
        if fusion:
            # 推理链路留在 meta.jsonl 里。**不能塞进 manifest**——verdict 与
            # detections[] 都是 additionalProperties: false，塞进去整份报文
            # 就校验不过了（上一轮 upload_failed 正是这么栽的）。
            p.meta.append(json.dumps(
                {"kind": "FUSION", "event_id": p.event_id, "verdict": fusion},
                ensure_ascii=False))
        before = p.before or {}
        after = p.after or {}
        verdict = decide_verdict(
            after.get("l2_reading"), before_conf=float(before.get("confidence", 0.0)),
            after_conf=float(after.get("confidence", 0.0)),
            defect_class=p.defect_class,
            is_anomaly=bool((l3 or {}).get("is_anomaly")),
            aborted=p.abort is not None,
            fusion=None if p.abort is not None else fusion)
        ctx = _Ctx(p)
        res = self.packer.pack(ctx, run_id=self.run_id, verdict=verdict,
                               meta_lines=p.meta)
        if res.ok:
            self.packed += 1
            g = res.manifest["gain"]
            self.log.info("证据包已落盘", event_id=p.event_id[:8],
                          verdict=verdict["result"],
                          delta_conf=g["delta_conf"],
                          density_ratio=g["pixel_density_ratio"])
        else:
            self.log.error("打包失败", event_id=p.event_id[:8], detail=res.error)
        self.pending.pop(p.event_id, None)

    def _harvest_aborted(self) -> None:
        """状态机一中止就立刻出包，不必等 TTL。

        中止的复核照样要打包上传——**复核失败的样本对调参最有价值**（ICD §6）。
        但只靠 60 s 的 TTL 兜底会漏：一轮巡检跑完收工时，最后几个中止事件还没
        到期就随进程一起没了。实测一轮里有一个中止事件只留下了 cruise 原图和
        mission_ctx.json，台账里查无此事。

        mission 进 ABORT 时就把 mission_ctx.json 写进证据目录了，看到它带
        abort 就可以立刻定案。
        """
        for eid in list(self.pending):
            p = self.pending[eid]
            if p.before is None or p.after is not None:
                continue
            f = Path(self.packer.root) / self.run_id / eid / "mission_ctx.json"
            try:
                m = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if m.get("abort"):
                self._finish(p)

    def flush(self) -> None:
        """收工前把还开着的复核结清。见 _harvest_aborted 的说明。"""
        for eid in list(self.pending):
            p = self.pending[eid]
            if p.before is None:
                continue
            f = Path(self.packer.root) / self.run_id / eid / "mission_ctx.json"
            if not f.exists():
                continue          # 没进入过复核，不出包
            if p.abort is None and p.after is None:
                p.abort = {"at_state": "VERIFY", "reason": "STATE_TIMEOUT",
                           "detail": "巡检收工时该次复核尚未完成"}
            self._finish(p)

    def _expire(self) -> None:
        """超时未收到 VERIFY 的事件按中止处理——复核失败的样本对调参最有价值。

        **但"根本没进入复核"和"复核失败"是两回事，不能混为一谈。**perception
        一检出可疑目标就分配 event_id，而 mission 那边可能因为同目标冷却、同
        巡检位已测过、恢复静默或预算耗尽而根本不发起复核（ICD §7.3）——那是
        抑制规则正常工作，不是失败。把它们也打成 INCONCLUSIVE 证据包，会让
        "复核成功率"这个指标同时统计"发起过的复核"和"压根没发起的可疑事件"，
        分母被抑制掉的事件撑大，指标就没有意义了：实测十个证据包里有四个是
        这么来的，成功率被从 100 % 拉到 60 %。

        判据是证据目录里有没有 mission_ctx.json——状态机只要真的开了一次复核
        （无论走到 PACK 还是 ABORT）就会写下它，被抑制的事件不会。
        """
        now = mono_ns()
        for eid in [k for k, p in self.pending.items()
                    if (now - p.t0_ns) / 1e9 > self.stale_s]:
            p = self.pending[eid]
            if p.before is None:
                self.pending.pop(eid, None)
                continue
            if not (Path(self.packer.root) / self.run_id / eid
                    / "mission_ctx.json").exists():
                # 巡航态那张原图留在盘上（cruise_raw.jpg 本来就是给重训练用
                # 的），但不出 manifest，也就不进台账、不计入复核成功率。
                self.log.info("可疑事件未进入复核，不出证据包",
                              event_id=eid[:8],
                              detail="被任务层抑制或预算耗尽，见 mission 日志")
                self.pending.pop(eid, None)
                continue
            if p.abort is None:
                # 真实的中止位置由 _merge_mission_ctx 从 mission_ctx.json 取；
                # 走到这里说明连那个文件都没有（mission 提前退出之类）。
                # reason 枚举是冻结的，只能落在 STATE_TIMEOUT 上，但 detail 必须
                # 讲清楚**是谁超时**——否则读 manifest 的人会以为状态机在 VERIFY
                # 卡了 30 s，而实际上 FSM 可能几秒前就在 AIM 里中止了。
                p.abort = {"at_state": "VERIFY", "reason": "STATE_TIMEOUT",
                           "detail": "uploader 侧超时：等待 %.0f s 未收到 stage=VERIFY "
                                     "的报文，且证据目录内没有 mission_ctx.json，"
                                     "状态机真实的中止位置未知" % self.stale_s}
            self._finish(p)

    def step(self) -> None:
        for m in self.det_sub.drain(max_n=64):
            self.on_detection(m)
        for m in self.status_sub.drain(max_n=128):
            self.on_status(m)
        self._harvest_aborted()
        self._expire()
        now = time.monotonic()
        if now - self._last_upload >= self.upload_period_s:
            self._last_upload = now
            for r in self.queue.drain(limit=4):
                if r.failed:
                    self.log.warn("部分文件上传失败", failed=len(r.failed))
            self.packer.enforce_retention()

    def serve_forever(self) -> None:
        self._running = True
        self.log.info("上传节点启动", evidence=str(self.packer.root),
                      transport=self.cfg.get("uploader.transport"))
        try:
            while self._running:
                self.step()
                time.sleep(0.1)
        finally:
            self.close()

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self._running = False
        try:
            self.flush()
        except Exception as e:                    # noqa: BLE001
            self.log.warn("收工结清失败", detail=str(e))
        self.det_sub.close()
        self.status_sub.close()
        self.queue.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="上传节点")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    node = UploaderNode(Config.load(a.config))
    signal.signal(signal.SIGINT, lambda *_: node.stop())
    signal.signal(signal.SIGTERM, lambda *_: node.stop())
    node.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
