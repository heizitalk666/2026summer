#!/usr/bin/env python3
"""接口一致性校验。ICD §10.1 的七项 + 差异清单 D5 的第八项。

    python -m patrol.tools.validate

D3 评审前这个脚本必须全绿。建议接进 CI，每次改 Schema 自动跑一遍。

七项来自 ICD §10.1：
  1. 五份 Schema 自身是否是合法的 Draft 2020-12
  2. 抽取 ICD 所有 json 代码块，按 msg_type 找到对应 Schema 并校验
  3. 复核像素密度算例（49.9 / 149.6 / 120.0 px，z_req，d_max，桩的 d_max）
  4. 复核时序预算加总是否等于 8.8 s，N_max 是否等于 22
  5. 检查每个状态的超时是否都大于其预算
  6. 比对 ICD 附录 D 内嵌的 Schema 与 schemas/ 下的文件是否一致
  7. 跑十一条反例，确认越界指令、协议外参数、自相矛盾的字段组合都被拦下

第八项是差异清单 D5 的增补：
  8. 网关硬编码常量 ↔ Schema 的 minimum/maximum 交叉比对

关于第 6 项：ICD 原文要求"逐字节一致"，但 markdown 围栏的缩进、行尾换行、
编辑器的尾随空白都会让它误报，而这类误报会训练出"红了就手工改一下附录"的
习惯，反而削弱这条检查。这里按差异清单 D4 的建议改为 json.loads 后深比较。
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from patrol.common import messages as M
from patrol.gateway import limits as L

REPO = Path(__file__).resolve().parents[2]
def _icd_path():
    """按 glob 找 ICD，升版本号改文件名时不用改代码（v1.0 → v2.0 就踩过一次）。"""
    hits = sorted((REPO / "docs").glob("ICD-RK3576-PATROL-v*.md"))
    if len(hits) != 1:
        raise RuntimeError("docs/ 下应当只有一份 ICD，实际 %d 份" % len(hits))
    return hits[0]


ICD = _icd_path()
ICD_VERSION = ICD.stem.rsplit("-v", 1)[1]
SCHEMA_DIR = REPO / "patrol" / "schemas"

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


class Report:
    def __init__(self) -> None:
        self.items: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append((name, ok, detail))
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  {mark}  {name}" + (f"  {DIM}{detail}{RESET}" if detail else ""))
        return ok

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.items if not ok)


def _icd_json_blocks() -> list:
    src = ICD.read_text(encoding="utf-8")
    out = []
    for b in re.findall(r"```json\n(.*?)\n```", src, re.S):
        try:
            out.append(json.loads(b))
        except Exception:
            out.append(None)          # §4.4 的 checks 片段不是完整 JSON，正常
    return out


def _dig(node, dotted: str):
    for part in dotted.split("."):
        node = node[part]
    return node


# ---------------------------------------------------------------- 1
def check_schemas_valid(r: Report) -> None:
    print(f"\n{YELLOW}[1] 五份 Schema 自身是合法的 Draft 2020-12{RESET}")
    files = sorted(SCHEMA_DIR.glob("*.schema.json"))
    r.add("Schema 文件齐全", len(files) == 5, f"{len(files)}/5 份")
    for f in files:
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(s)
            r.add(f.name, True)
        except Exception as e:                              # noqa: BLE001
            r.add(f.name, False, str(e)[:90])


# ---------------------------------------------------------------- 2
def check_examples(r: Report) -> None:
    print(f"\n{YELLOW}[2] ICD 内嵌示例报文按 msg_type 校验{RESET}")
    examples = [b for b in _icd_json_blocks() if b and "msg_type" in b]
    r.add("示例报文条数", len(examples) == 6, f"{len(examples)} 条（ICD §10.1 称 6 条）")
    for ex in examples:
        mt = ex["msg_type"]
        try:
            M.validate(ex, mt)
            r.add(f"正例 {mt}", True)
        except M.SchemaViolation as e:
            r.add(f"正例 {mt}", False, str(e)[:90])


# ---------------------------------------------------------------- 3
def check_pixel_density(r: Report) -> None:
    print(f"\n{YELLOW}[3] 像素密度算例{RESET}")
    # W / P_MIN / theta 取自配置而不是写死：下面那几个期望值（49.9、149.6、
    # z_req=2.41、d_max=6.24）是方案书 §5.3 按 1920 px / 60° / 120 px 推出来
    # 的。改了相机配置还想让这一节绿，就说明推导没跟着重算——camera.yaml 自己
    # 写着"偏差超过 3° 时 d_max 与路线标定规范必须按实测值重算并通报全组"。
    # 写死就正好把这个信号屏蔽掉了。
    from patrol.common.config import Config
    cfg = Config.load()
    W = float(cfg.get("camera.width", 1920))
    theta = float(cfg.get("optics.hfov_at_1x_deg", 60.0))
    P_MIN = float(cfg.get("optics.pixel_density_min_px", 120.0))
    D = 0.15                       # 指针表盘直径先验，见 CLASS_SIZE_M
    tan_half = math.tan(math.radians(theta) / 2.0)

    def p(z, d):
        return W * D * z / (2.0 * d * tan_half)

    def dmax(z, need):
        return W * D * z / (2.0 * need * tan_half)

    cases = [
        ("巡航态遇见指针表 p(z=1,d=5)", p(1, 5), 49.9, 0.1),
        ("复核态 3× 变焦 p(z=3,d=5)", p(3, 5), 149.6, 0.1),
        ("距离上限校核 p(z=3,d=6.24)", p(3, 6.24), 120.0, 0.2),
        ("所需变焦倍率 z_req", P_MIN / p(1, 5), 2.41, 0.01),
        ("真机 d_max (z=3)", dmax(3, P_MIN), 6.24, 0.01),
        ("桩 d_max (z=3, k=2/3)", dmax(3, P_MIN / (2.0 / 3.0)), 4.16, 0.01),
    ]
    for name, got, want, tol in cases:
        r.add(name, abs(got - want) <= tol, f"{got:.2f} (期望 {want})")

    r.add("硬约束 z ≥ 3", 3.0 >= P_MIN / p(1, 5),
          f"z_req={P_MIN / p(1, 5):.2f} → 取 3 留余量")
    r.add("硬约束 d ≤ 6 m", dmax(3, P_MIN) >= 6.0,
          f"d_max={dmax(3, P_MIN):.2f} → 取 6 留余量")


# ---------------------------------------------------------------- 4
def check_budget(r: Report) -> None:
    print(f"\n{YELLOW}[4] 时序预算与复核预算{RESET}")
    # D3 决议 C10（SUSPECT 三帧 0.2→0.3）与 C4（按需变焦 ZOOM 1.2→1.5）之后的值。
    icd_budget = {"SUSPECT": 0.3, "HALT_REQ": 2.0, "AIM": 1.5, "ZOOM": 1.5,
                  "CAPTURE": 0.6, "VERIFY": 2.5, "PACK": 0.5, "RESUME": 0.3}
    T_r = sum(icd_budget.values())
    r.add("ICD §7.2 预算加总 T_r = 9.2 s", abs(T_r - 9.2) < 1e-9, f"{T_r:.1f} s")

    L_m, v, T_max = 200.0, 0.5, 600.0
    n = math.floor((T_max - L_m / v) / T_r)
    r.add("ICD §7.4 N_max = 21", n == 21, f"{n}")

    # 当前配置的实际值。**这里必须读配置，不能沿用上面 ICD 算例的 L/v/T_max。**
    # 实车最高只有 0.3 m/s（底盘固件 MAX_SPD 0.1 × 三档，见 docs/底盘固件评审.md），
    # 而原来这一段把算例的 0.5 m/s 也套在配置上，于是"200 m 路线在本车上根本
    # 跑不完"这件事被算例的数字盖住了，一直没人发现。
    from patrol.common.config import Config
    cfg = Config.load()
    T_cfg = sum(cfg.get("mission.fsm.budget_s").values())
    bud = cfg.get("mission.budget")
    L_cfg = float(bud["route_length_m"])
    v_cfg = float(bud["cruise_speed_mps"])
    T_max_cfg = float(bud["max_run_seconds"])
    cruise_s = L_cfg / max(1e-6, v_cfg)
    n_cfg = math.floor((T_max_cfg - cruise_s) / T_cfg)
    r.add("当前配置 N_max > 0（路线跑得完且排得下复核）", n_cfg > 0,
          f"L={L_cfg:.0f} m, v={v_cfg:.2f} m/s → 巡航 {cruise_s:.0f} s / {T_max_cfg:.0f} s，"
          f"T_r={T_cfg:.1f} s，N_max={n_cfg}")
    if n_cfg <= 0:
        print(f"  {DIM}·  光巡航就要 {cruise_s:.0f} s，超过单轮上限 {T_max_cfg:.0f} s，"
              f"一次复核都排不下。缩短路线、放宽 T_max 或提高车速三选一{RESET}")


# ---------------------------------------------------------------- 5
def check_timeouts(r: Report) -> None:
    print(f"\n{YELLOW}[5] 每个状态的超时都大于其预算{RESET}")
    budget = {"SUSPECT": 0.3, "HALT_REQ": 2.0, "AIM": 1.5, "ZOOM": 1.5,
              "CAPTURE": 0.6, "VERIFY": 2.5, "PACK": 0.5, "RESUME": 0.3}
    # CAPTURE 超时 1.5→4.0 是 D3 决议 A3 的连锁：条件式三视角走辅视角那条路
    # 要 2.1 s，1.5 s 会让它必然超时进 ABORT。
    timeout = {"SUSPECT": 0.5, "HALT_REQ": 4.0, "AIM": 3.0, "ZOOM": 2.5,
               "CAPTURE": 4.0, "VERIFY": 5.0, "PACK": 2.0, "RESUME": 1.0}
    bad = [s for s in budget if timeout[s] <= budget[s]]
    r.add("ICD §7.2 八个状态全部自洽", not bad,
          "违反: " + ", ".join(bad) if bad else "超时 > 预算")

    from patrol.common.config import Config
    cfg = Config.load()
    cb, ct = cfg.get("mission.fsm.budget_s"), cfg.get("mission.fsm.timeout_s")
    bad2 = [s for s in cb if ct.get(s, 0) <= cb[s]]
    r.add("当前配置八个状态自洽", not bad2,
          "违反: " + ", ".join(bad2) if bad2 else "超时 > 预算")


# ---------------------------------------------------------------- 6
#: 相对 ICD 附录 D 允许存在的差异，逐条对应差异清单里的决议。
#: 不在这张表里的差异一律判失败——目的是让偏移**可见**，而不是掩盖它。
#:
#: **D3 评审后这张表是空的。** A1/A3/A4/B3/D1/D2/D3 六条决议全部落地进 ICD v2.0
#: 正文，附录 D 由 ``patrol/tools/sync_icd_appendix.py`` 从 ``patrol/schemas/``
#: 直接生成，不再有"已知但未收编"的偏移。表保留着是因为下一轮评审之间还会再出现
#: 这种状态——**新的偏移必须登记在这里并注明对应哪条待决议项**，不登记就判失败。
ALLOWED_DRIFT: dict[str, list[tuple[str, str]]] = {}


def _diff_paths(a, b, prefix="") -> list[str]:
    """列出 b 相对 a 多出／少了／改了的路径。"""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            pa = f"{prefix}.{k}" if prefix else k
            if k not in a:
                out.append(pa)
            elif k not in b:
                out.append("-" + pa)
            else:
                out.extend(_diff_paths(a[k], b[k], pa))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) == len(b):
            # 等长时逐位递归。JSON Schema 里 oneOf/allOf 的位置是有意义的，
            # 整块比对会把"枚举里多了一个值"报成"整个分支被换掉"。
            for i, (x, y) in enumerate(zip(a, b)):
                out.extend(_diff_paths(x, y, f"{prefix}[{i}]"))
        elif a != b:
            for x in b:
                if x not in a:
                    out.append(f"{prefix}[+{x}]")
            for x in a:
                if x not in b:
                    out.append(f"-{prefix}[{x}]")
    elif a != b:
        out.append(prefix)
    return out


def check_embedded_copies(r: Report) -> None:
    print(f"\n{YELLOW}[6] schemas/ 相对 ICD 附录 D 的差异必须都在决议清单内{RESET}")
    embedded = {b["title"]: b for b in _icd_json_blocks()
                if b and "$schema" in b and "title" in b}
    for mt, fn in M.SCHEMA_FILES.items():
        onfile = json.loads((SCHEMA_DIR / fn).read_text(encoding="utf-8"))
        title = onfile.get("title")
        emb = embedded.get(title)
        if emb is None:
            r.add(fn, False, f"附录 D 里找不到 title={title}")
            continue
        # $comment 是给人看的注释，不参与比对
        diffs = [d for d in _diff_paths(emb, onfile)
                 if not d.split(".")[-1].startswith("$comment")]
        allowed = {p for p, _ in ALLOWED_DRIFT.get(title, [])}
        unexpected = [d for d in diffs if d not in allowed]
        if not diffs:
            r.add(fn, True, "与附录 D 一致")
        elif not unexpected:
            r.add(fn, True, f"{len(diffs)} 处差异，均在决议清单内")
            for p_, why in ALLOWED_DRIFT.get(title, []):
                if p_ in diffs:
                    print(f"  {DIM}·  {p_}  —  {why}{RESET}")
        else:
            r.add(fn, False, "未登记的差异: " + "; ".join(unexpected[:3]))


# ---------------------------------------------------------------- 7
def check_counterexamples(r: Report) -> None:
    print(f"\n{YELLOW}[7] 十一条反例必须全部被拦下{RESET}")
    import copy
    ex = {b["msg_type"]: b for b in _icd_json_blocks() if b and "msg_type" in b}
    acks = [b for b in _icd_json_blocks() if b and b.get("msg_type") == "COMMAND_ACK"]
    ack_ok = next(a for a in acks if a["result"] == "ACCEPTED")

    def mut(base, fn):
        o = copy.deepcopy(base)
        fn(o)
        return o

    cases = [
        ("CREEP_FORWARD.distance_m = 1.20", "CONTROL_COMMAND",
         mut(ex["CONTROL_COMMAND"], lambda o: (o.update(command="CREEP_FORWARD",
                                                        params={"distance_m": 1.20})))),
        ("PTZ_SET.zoom = 5.0", "CONTROL_COMMAND",
         mut(ex["CONTROL_COMMAND"], lambda o: o["params"].update(zoom=5.0))),
        ('command = "SET_SPEED"', "CONTROL_COMMAND",
         mut(ex["CONTROL_COMMAND"], lambda o: o.update(command="SET_SPEED",
                                                       params={"speed_mps": 0.8}))),
        ("PAUSE.params 夹带 steer_deg", "CONTROL_COMMAND",
         mut(ex["CONTROL_COMMAND"], lambda o: o.update(
             command="PAUSE", params={"reason": "VERIFY_REQUEST", "steer_deg": 12.0}))),
        ("ACCEPTED 却带 reject_code", "COMMAND_ACK",
         mut(ack_ok, lambda o: o.update(reject_code="PARAM_OUT_OF_RANGE"))),
        ("SAFETY_EVENT 但 safety = null", "STATUS_REPORT",
         mut(ex["STATUS_REPORT"], lambda o: o.update(report_kind="SAFETY_EVENT",
                                                     safety=None))),
        # D1：150 ms 是「超标但真实」的实测值，Schema 放行、由网关判逻辑；
        # Schema 只该挡负数这种物理上不可能的量。
        ("brake_latency_ms = -1", "STATUS_REPORT",
         mut(ex["STATUS_REPORT"], lambda o: o["safety"].update(brake_latency_ms=-1))),
        ("PREEMPTED 却带 reject_code", "COMMAND_ACK",
         mut(ack_ok, lambda o: o.update(result="PREEMPTED",
                                        reject_code="PARAM_OUT_OF_RANGE"))),
        ("证据包 after.l2_reading 的 kind 不在枚举里", "EVIDENCE_PACKAGE",
         mut(ex["EVIDENCE_PACKAGE"], lambda o: o["after"].update(
             l2_reading={"kind": "NO_SUCH_KIND", "junk": 1}))),
        ("中止的复核标记 verify_success = true", "EVIDENCE_PACKAGE",
         mut(ex["EVIDENCE_PACKAGE"], lambda o: (
             o.update(abort={"at_state": "ZOOM", "reason": "STATE_TIMEOUT",
                             "detail": "x"}),
             o["gain"].update(verify_success=True)))),
        ("is_suspect = true 但 event_id = null", "DETECTION_EVENT",
         mut(ex["DETECTION_EVENT"], lambda o: o.update(event_id=None))),
    ]
    caught = 0
    for name, mt, doc in cases:
        try:
            M.validate(doc, mt)
            r.add(f"反例「{name}」", False, "漏放！")
        except M.SchemaViolation:
            r.add(f"反例「{name}」", True, "已拦下")
            caught += 1
    r.add("十一条反例全部拦下", caught == len(cases), f"{caught}/{len(cases)}")


# ---------------------------------------------------------------- 8
def check_gateway_vs_schema(r: Report) -> None:
    print(f"\n{YELLOW}[8] 网关硬编码常量 ↔ Schema 范围交叉比对（差异清单 D5）{RESET}")
    for const_name, (lo, hi), fn, path in L.SCHEMA_CROSSCHECK:
        schema = json.loads((SCHEMA_DIR / fn).read_text(encoding="utf-8"))
        try:
            node = _dig(schema, path)
        except (KeyError, TypeError):
            r.add(f"{const_name} → {fn}", False, f"Schema 里找不到 {path}")
            continue
        s_lo, s_hi = node.get("minimum"), node.get("maximum")
        ok = (s_lo is not None and abs(float(s_lo) - lo) < 1e-9
              and s_hi is not None and abs(float(s_hi) - hi) < 1e-9)
        r.add(f"{const_name} → {fn}", ok,
              f"网关 [{lo}, {hi}] vs Schema [{s_lo}, {s_hi}]")

    # 白名单与 Schema 的 command 枚举必须一致
    cc = json.loads((SCHEMA_DIR / "control_command.schema.json").read_text(encoding="utf-8"))
    enum = set(cc["properties"]["command"]["enum"])
    allowed = set(L.WHITELIST_WITH_RATE)
    r.add("白名单 ⊆ Schema command 枚举", L.WHITELIST <= enum,
          f"Schema {len(enum)} 条，网关基础白名单 {len(L.WHITELIST)} 条")
    # A1 已决议采纳：PTZ_RATE 必须在 Schema 里，否则速率通路开了也发不出合法报文。
    r.add("A1 速率指令已写入 Schema", allowed <= enum,
          "缺: " + ", ".join(sorted(allowed - enum)) if allowed - enum
          else "PTZ_RATE 在 command 枚举内")


def main() -> int:
    print(f"{YELLOW}接口一致性校验  ICD-RK3576-PATROL v{ICD_VERSION}"
          f"  报文 schema_version {M.SCHEMA_VERSION if hasattr(M, 'SCHEMA_VERSION') else __import__('patrol').SCHEMA_VERSION}{RESET}")
    r = Report()
    for fn in (check_schemas_valid, check_examples, check_pixel_density,
               check_budget, check_timeouts, check_embedded_copies,
               check_counterexamples, check_gateway_vs_schema):
        try:
            fn(r)
        except Exception as e:                              # noqa: BLE001
            r.add(f"{fn.__name__} 执行异常", False, repr(e)[:120])

    total = len(r.items)
    print()
    if r.failed == 0:
        print(f"{GREEN}PASS{RESET}  Schema 5 份、正例 6 条、反例 11 条、"
              f"内嵌副本一致、算例与预算全部自洽、网关常量与 Schema 同步"
              f"（共 {total} 项）")
        return 0
    print(f"{RED}FAIL{RESET}  {r.failed}/{total} 项未通过")
    return 1


if __name__ == "__main__":
    sys.exit(main())
