"""指令的五项校验。ICD §4.3 / §4.4。

checks 逐项列出网关做了哪些校验，每项取 PASS / FAIL / SKIP。这个字段看起来
冗余，但它让"网关到底有没有在校验"这件事**可观测**：评审和验收时抽查日志，
如果某一项长期是 SKIP，说明那层校验根本没接上。

越界一律拒绝而不截断。截断会让 AI 侧的 bug 静默通过：发了 5 m 的
CREEP_FORWARD，被截成 0.5 m 照常执行，联调时看不出问题，等到某次截断逻辑
失效就出事。拒绝会立刻暴露在日志里。
"""
from __future__ import annotations

from patrol.common import messages as M
from patrol.gateway import limits as L

CheckResult = tuple[bool, str | None, str | None]   # (ok, reject_code, detail)
_OK: CheckResult = (True, None, None)


def check_whitelist(cmd: dict, *, allow_ptz_rate: bool) -> CheckResult:
    """第一层：AI 侧能表达什么。

    协议中不存在转向角、轮速、扭矩、制动力、目标速度这类量。AI 侧没有任何
    字段可以直接指定车怎么动，只能表达"想停"和"想去某个已标定的点"。
    """
    allowed = L.WHITELIST_WITH_RATE if allow_ptz_rate else L.WHITELIST
    c = cmd.get("command")
    if c not in allowed:
        return False, "NOT_IN_WHITELIST", "指令 %r 不在白名单内" % c
    return _OK


def check_schema(cmd: dict) -> CheckResult:
    """第二层：报文结构是否合法。"""
    try:
        M.check_version(cmd)
    except M.VersionMismatch as e:
        return False, "SCHEMA_VERSION_MISMATCH", str(e)
    # PTZ_RATE 是差异清单 A1 的增补，尚未写入冻结的 Schema，单独校验
    if cmd.get("command") == "PTZ_RATE":
        return _check_ptz_rate_shape(cmd)
    # 只查结构，数值范围交给 check_range（依据是网关硬编码常量）。
    # 这样越界指令的 ACK 才会是 schema:PASS + range:FAIL + PARAM_OUT_OF_RANGE，
    # 与 ICD §4.6 的拒绝示例一致。
    try:
        M.validate_structure(cmd, "CONTROL_COMMAND")
    except M.SchemaViolation as e:
        msg = str(e)
        code = "PARAM_MISSING" if "is a required property" in msg else "SCHEMA_INVALID"
        return False, code, msg
    return _OK


def _check_ptz_rate_shape(cmd: dict) -> CheckResult:
    p = cmd.get("params")
    if not isinstance(p, dict):
        return False, "SCHEMA_INVALID", "params 不是对象"
    need = {"pan_dps", "tilt_dps", "ttl_ms"}
    missing = need - set(p)
    if missing:
        return False, "PARAM_MISSING", "缺少 %s" % ", ".join(sorted(missing))
    extra = set(p) - need
    if extra:
        return False, "SCHEMA_INVALID", "多余字段 %s" % ", ".join(sorted(extra))
    return _OK


def check_range(cmd: dict, *, known_waypoints: frozenset[str]) -> CheckResult:
    """第三层：参数是否在网关硬编码的范围内。

    范围值来自 gateway/limits.py，不从任何配置读取（ICD §4.3）。
    """
    c = cmd.get("command")
    p = cmd.get("params") or {}

    # 任何指令都不许夹带底层控制量，即使 command 本身合法
    bad = sorted(set(p) & L.FORBIDDEN_PARAMS)
    if bad:
        return False, "SCHEMA_INVALID", "params 夹带协议外的底层控制量: %s" % ", ".join(bad)

    if c == "PAUSE":
        r = p.get("reason")
        if r not in L.PAUSE_REASONS:
            return False, "PARAM_OUT_OF_RANGE", "PAUSE.reason=%r 不在允许值内" % r
    elif c == "RESUME":
        if p:
            return False, "SCHEMA_INVALID", "RESUME 不接受任何参数"
    elif c == "CREEP_FORWARD":
        d = p.get("distance_m")
        if d is None:
            return False, "PARAM_MISSING", "缺少 distance_m"
        if not L.in_range(d, L.CREEP_DISTANCE_M):
            return False, "PARAM_OUT_OF_RANGE", (
                "CREEP_FORWARD.distance_m=%.3f exceeds [%.2f,%.2f]"
                % (float(d), *L.CREEP_DISTANCE_M))
    elif c == "GOTO_OBSERVE":
        wp = p.get("waypoint_id")
        if wp not in known_waypoints:
            return False, "UNKNOWN_WAYPOINT", "巡检位 %r 不在网关标定表内" % wp
        tol = p.get("tolerance_m")
        if tol is None:
            return False, "PARAM_MISSING", "缺少 tolerance_m"
        if not L.in_range(tol, L.GOTO_TOLERANCE_M):
            return False, "PARAM_OUT_OF_RANGE", (
                "GOTO_OBSERVE.tolerance_m=%.3f exceeds [%.2f,%.2f]"
                % (float(tol), *L.GOTO_TOLERANCE_M))
    elif c == "PTZ_SET":
        for key, bounds in (("pan_deg", L.PTZ_PAN_DEG), ("tilt_deg", L.PTZ_TILT_DEG),
                            ("zoom", L.PTZ_ZOOM)):
            v = p.get(key)
            if v is None:
                return False, "PARAM_MISSING", "缺少 %s" % key
            if not L.in_range(v, bounds):
                return False, "PARAM_OUT_OF_RANGE", (
                    "PTZ_SET.%s=%.3f exceeds [%.1f,%.1f]" % (key, float(v), *bounds))
        if p.get("speed") not in L.PTZ_SPEEDS:
            return False, "PARAM_OUT_OF_RANGE", "PTZ_SET.speed=%r 不在允许值内" % p.get("speed")
    elif c == "PTZ_RATE":
        for key, bounds in (("pan_dps", L.PTZ_PAN_DPS), ("tilt_dps", L.PTZ_TILT_DPS)):
            v = p.get(key)
            if not L.in_range(v, bounds):
                return False, "PARAM_OUT_OF_RANGE", (
                    "PTZ_RATE.%s=%.2f exceeds [%.1f,%.1f]" % (key, float(v), *bounds))
        if not L.in_range(p.get("ttl_ms"), L.PTZ_RATE_TTL_MS):
            return False, "PARAM_OUT_OF_RANGE", (
                "PTZ_RATE.ttl_ms=%s exceeds [%d,%d]" % (p.get("ttl_ms"), *L.PTZ_RATE_TTL_MS))
    elif c == "HEARTBEAT":
        if p.get("mission_state") not in L.MISSION_STATES:
            return False, "PARAM_OUT_OF_RANGE", (
                "HEARTBEAT.mission_state=%r 不是十个状态之一" % p.get("mission_state"))

    if not L.in_range(cmd.get("timeout_ms", 0), L.TIMEOUT_MS):
        return False, "PARAM_OUT_OF_RANGE", "timeout_ms=%s 越界" % cmd.get("timeout_ms")
    if cmd.get("issued_by") not in L.ISSUED_BY:
        return False, "PARAM_OUT_OF_RANGE", "issued_by=%r 不在允许值内" % cmd.get("issued_by")
    return _OK


def check_state_conflict(cmd: dict, chassis_state: str) -> CheckResult:
    """第四层：当前底盘状态是否接受该指令。"""
    c = cmd.get("command")
    if chassis_state == "ESTOP" and c in ("RESUME", "CREEP_FORWARD", "GOTO_OBSERVE"):
        # 急停必须人工解除，不尝试恢复
        return False, "ESTOP_ACTIVE", "急停生效中，运动指令一律拒绝"
    if chassis_state == "FAULT" and c in ("CREEP_FORWARD", "GOTO_OBSERVE"):
        return False, "STATE_CONFLICT", "底盘 FAULT 状态不接受新的运动指令"
    return _OK


def check_safety_override(cmd: dict, *, safety_active: bool) -> CheckResult:
    """第五层：底盘安全层是否正在介入。

    安全事件生效期间，网关拒绝一切运动指令。云台指令不受影响——云台动作
    不改变车辆运动，而复核中止时恰恰需要把云台归位。
    """
    if not safety_active:
        return _OK
    if cmd.get("command") in ("RESUME", "CREEP_FORWARD", "GOTO_OBSERVE"):
        return False, "SAFETY_OVERRIDE", "底盘安全层介入中，运动指令一律拒绝"
    return _OK
