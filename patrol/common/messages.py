"""四条接口的报文构造与校验。ICD §3–§6。

设计要点：

1. **发送前校验，不是接收后才发现。**每个 build_* 都可以在构造后立刻过一遍
   Schema（strict 模式），这样字段拼错会在发送方当场炸掉，而不是变成接收方
   一条难查的 KeyError。
2. **版本不匹配直接丢弃，不做兼容性猜测。**ICD §2.6：接收方在解析前检查主
   版本号，不匹配时丢弃报文并上报 SafetyEvent(SCHEMA_VERSION_MISMATCH)。
3. **Schema 是报文层校验，网关常量是执行层校验。**两者都要有（纵深防御），
   由 tools/validate.py 交叉比对保证同步（差异清单 D5）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from patrol import SCHEMA_VERSION

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"

SCHEMA_FILES = {
    "DETECTION_EVENT":  "detection_event.schema.json",
    "CONTROL_COMMAND":  "control_command.schema.json",
    "COMMAND_ACK":      "command_ack.schema.json",
    "STATUS_REPORT":    "status_report.schema.json",
    "EVIDENCE_PACKAGE": "evidence_package.schema.json",
}


class SchemaViolation(ValueError):
    """报文不符合 Schema。带上首条错误的字段路径，便于定位。"""

    def __init__(self, msg_type: str, errors: list):
        self.msg_type = msg_type
        self.errors = errors
        first = errors[0]
        path = "/".join(str(p) for p in first.absolute_path) or "<root>"
        super().__init__("%s 校验失败 @ %s: %s" % (msg_type, path, first.message))


class VersionMismatch(ValueError):
    """主版本号不一致。按 ICD §2.6 应丢弃报文并上报 SafetyEvent。"""


@lru_cache(maxsize=None)
def load_schema(msg_type: str) -> dict:
    fn = SCHEMA_FILES.get(msg_type)
    if fn is None:
        raise KeyError("未知报文类型: %s" % msg_type)
    with open(SCHEMA_DIR / fn, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def validator_for(msg_type: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(msg_type))


def validate(msg: dict, msg_type: str | None = None) -> dict:
    """校验一条报文，通过则原样返回，否则抛 SchemaViolation。"""
    mt = msg_type or msg.get("msg_type")
    errs = sorted(validator_for(mt).iter_errors(msg), key=lambda e: list(e.absolute_path))
    if errs:
        raise SchemaViolation(mt, errs)
    return msg


def check_version(msg: dict) -> None:
    """ICD §2.6：只比主版本号，不匹配直接丢弃，不做兼容性猜测。"""
    got = str(msg.get("schema_version", ""))
    if got.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
        raise VersionMismatch(
            "主版本号不匹配: 收到 %r，本进程 %r" % (got, SCHEMA_VERSION)
        )


def _head(msg_type: str, seq: int, ts_mono_ns: int, ts_utc_ms: int,
          run_id: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "msg_type": msg_type,
        "seq": seq,
        "ts_mono_ns": ts_mono_ns,
        "ts_utc_ms": ts_utc_ms,
        "run_id": run_id,
    }


# --------------------------------------------------------------------------
# IF-1 DetectionEvent
# --------------------------------------------------------------------------
def build_detection_event(
    *, seq: int, ts_mono_ns: int, ts_utc_ms: int, run_id: str,
    event_id: str | None, stage: str, model: dict, context: dict,
    detections: list, suspect: dict, latency_ms: dict,
    l3_anomaly: dict | None = None, strict: bool = True,
) -> dict:
    msg = _head("DETECTION_EVENT", seq, ts_mono_ns, ts_utc_ms, run_id)
    msg.update({
        "event_id": event_id,
        "stage": stage,
        "model": model,
        "context": context,
        "detections": detections,
        "suspect": suspect,
        "latency_ms": latency_ms,
    })
    if l3_anomaly is not None:
        msg["l3_anomaly"] = l3_anomaly
    return validate(msg) if strict else msg


def make_suspect(*, is_suspect: bool = False, trigger_rule: str | None = None,
                 target_track_id: int | None = None, severity: float = 0.0,
                 novelty: float = 0.0, priority: float = 0.0,
                 suppressed_by: str | None = None) -> dict:
    """suspect 块。priority = severity × confidence × novelty 由调用方算好传入。"""
    return {
        "is_suspect": is_suspect,
        "trigger_rule": trigger_rule,
        "target_track_id": target_track_id,
        "severity": round(float(severity), 6),
        "novelty": round(float(novelty), 6),
        "priority": round(float(priority), 6),
        "suppressed_by": suppressed_by,
    }


# --------------------------------------------------------------------------
# IF-2 ControlCommand / CommandAck
# --------------------------------------------------------------------------
def build_command(
    *, cmd_id: str, seq: int, ts_mono_ns: int, ts_utc_ms: int, run_id: str,
    event_id: str | None, issued_by: str, command: str, params: dict,
    timeout_ms: int, strict: bool = True,
) -> dict:
    msg = _head("CONTROL_COMMAND", seq, ts_mono_ns, ts_utc_ms, run_id)
    msg.update({
        "cmd_id": cmd_id,
        "event_id": event_id,
        "issued_by": issued_by,
        "command": command,
        "params": params,
        "timeout_ms": int(timeout_ms),
    })
    return validate(msg) if strict else msg


CHECK_KEYS = ("whitelist", "schema", "range", "state_conflict", "safety_override")


def all_checks(value: str = "SKIP") -> dict:
    return {k: value for k in CHECK_KEYS}


def build_ack(
    *, cmd_id: str, ts_mono_ns: int, result: str, checks: dict,
    reject_code: str | None = None, reject_detail: str | None = None,
    exec_handle: str | None = None, strict: bool = True,
) -> dict:
    msg = {
        "schema_version": SCHEMA_VERSION,
        "msg_type": "COMMAND_ACK",
        "cmd_id": cmd_id,
        "ts_mono_ns": ts_mono_ns,
        "result": result,
        "reject_code": reject_code,
        "reject_detail": (reject_detail or None) and reject_detail[:256],
        "checks": checks,
        "exec_handle": exec_handle,
    }
    return validate(msg) if strict else msg


# --------------------------------------------------------------------------
# IF-3 StatusReport
# --------------------------------------------------------------------------
def build_status_report(
    *, seq: int, ts_mono_ns: int, ts_utc_ms: int, run_id: str,
    report_kind: str, chassis: dict, ptz: dict, pose: dict, watchdog: dict,
    exec_: dict | None = None, safety: dict | None = None,
    strict: bool = True,
) -> dict:
    """无论 report_kind 是什么，chassis/ptz/pose/watchdog 四块都带完整快照。

    ICD §5.1：这样从任意一条报文都能还原当时的完整状态，排查时不用去拼
    上一条周期报文。
    """
    msg = _head("STATUS_REPORT", seq, ts_mono_ns, ts_utc_ms, run_id)
    msg.update({
        "report_kind": report_kind,
        "chassis": chassis,
        "ptz": ptz,
        "pose": pose,
        "watchdog": watchdog,
        "exec": exec_,
        "safety": safety,
    })
    return validate(msg) if strict else msg


# --------------------------------------------------------------------------
# IF-4 EvidencePackage
# --------------------------------------------------------------------------
def build_evidence_package(
    *, run_id: str, event_id: str, waypoint_id: str, ts_utc_ms: int,
    verdict: dict, before: dict, after: dict, gain: dict, timeline: list,
    files: list, abort: dict | None = None, strict: bool = True,
) -> dict:
    msg = {
        "schema_version": SCHEMA_VERSION,
        "msg_type": "EVIDENCE_PACKAGE",
        "run_id": run_id,
        "event_id": event_id,
        "waypoint_id": waypoint_id,
        "ts_utc_ms": ts_utc_ms,
        "verdict": verdict,
        "before": before,
        "after": after,
        "gain": gain,
        "timeline": timeline,
        "files": files,
        "abort": abort,
    }
    return validate(msg) if strict else msg


def snapshot(*, confidence: float, pixel_density_px: float, zoom: float,
             est_distance_m: float, defect_class: str | None,
             l2_reading: dict | None) -> dict:
    """before / after 用的精简检测结果，两者结构相同，方便直接做差。"""
    return {
        "confidence": round(float(confidence), 4),
        "pixel_density_px": round(float(pixel_density_px), 2),
        "zoom": round(float(zoom), 3),
        "est_distance_m": round(float(est_distance_m), 3),
        "defect_class": defect_class,
        "l2_reading": l2_reading,
    }


def compute_gain(before: dict, after: dict, *, verdict_result: str,
                 aborted: bool) -> dict:
    """复核增益指标。ICD §6.4，答辩三项关键指标全部落在这里。

    delta_conf 对 FALSE_ALARM 会是负值，这是正常的：复核把一个 0.41 的误检
    压到 0.05。统计时必须按 verdict.result 分组，混在一起算均值会接近零，
    看上去像是复核没起作用。
    """
    p_before = float(before.get("pixel_density_px") or 0.0)
    p_after = float(after.get("pixel_density_px") or 0.0)
    ratio = (p_after / p_before) if p_before > 0 else 0.0
    delta = float(after.get("confidence", 0.0)) - float(before.get("confidence", 0.0))
    return {
        "delta_conf": round(max(-1.0, min(1.0, delta)), 4),
        "pixel_density_ratio": round(ratio, 4),
        "verify_success": (not aborted) and verdict_result != "INCONCLUSIVE",
    }
