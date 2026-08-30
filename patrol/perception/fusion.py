"""L4 决策融合：四路模型说完话之后，由谁拍板。

**这一层没有权重，是纯规则的——这是有意的选择，不是偷懒。**

前面三层（检测、读数、异常）都可以是学出来的黑箱，因为它们的输出还能被
下一层质疑。但最后这一步的产物要进证据包、要送到人面前、要作为"这台设备
有没有问题"的记录留档。评审第一个问题一定是"凭什么"，而一个学出来的融合
网络对这个问题的回答只能是"它就是这么算的"。所以仲裁必须能逐条讲清楚：
哪一路说了什么、哪条规则命中了、为什么这一路压过了那一路。`reasons` 字段
就是干这个的，它会原样进 meta.jsonl。

四路证据的可信度排序（这个顺序是整层的核心，排错了会有实测后果）：

1. **互证冲突压过一切。**OCR 从表面上读到的量程/单位与标定先验对不上，
   意味着要么标定表错了、要么车站错了地方看错了表。这时候几何法算出的
   读数即使数值上完全正常也是**错的**，而且错得没有征兆——报出去比不报
   危险得多。所以冲突时直接判 INCONCLUSIVE 交人。
2. **有读数就以读数为准，L3 排在它后面。**L3 是非监督的，只学过"看起来
   正常"，对一块读数明确且在带内的表报异常多半是光照或视角变化引起的。
   实测把 L3 排在前面的后果：一整轮里压力表全被判成 UNKNOWN_ANOMALY，
   读数通路等于白做。但 L3 的意见不丢——转成 needs_human_review
   （ICD §3.1：L3 输出只允许进人工复核队列，不得直接告警）。
3. **观测条件不足时不下读数类结论。**像素密度没到判据线就谈读数精度是
   自欺欺人（0.5 % FS 需要 120 px，5 m 处 1× 只有 50 px）。这条只约束
   读数类结论——渗油、异物这类缺陷本来就不靠像素密度。
4. **证据不足就说不足。**这和网关"拒绝而非截断"是同一条原则：宁可交给人，
   不猜一个看起来合理的答案。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from patrol.perception.reading.nameplate import (CrossCheck,
                                                 canon_switch_state)

#: 六种结论，与 evidence_package.schema.json 的 verdict.result 枚举一一对应。
RESULTS = ("CONFIRMED_DEFECT", "FALSE_ALARM", "READING_OK", "READING_ABNORMAL",
           "UNKNOWN_ANOMALY", "INCONCLUSIVE")

#: 判 CONFIRMED_DEFECT 所需的复核后置信度；再往上一档判 CRITICAL。
CONFIRM_THR = 0.60
CRITICAL_THR = 0.85
#: 复核后置信度跌到复核前的这个比例以下，判误报消解。
FALSE_ALARM_DROP = 0.60
#: 读数类结论所需的最低像素密度，取判据线的比例。留 20 % 余量是因为
#: 密度是按 bbox 估的距离反算的，本身有几个百分点的抖动。
DENSITY_FLOOR_FRAC = 0.80


@dataclass
class Evidence:
    """四路模型 + 观测条件。**每个字段都可以是 None——缺席是常态，不是异常。**"""

    # L1 检测
    defect_class: str | None = None
    conf_before: float = 0.0
    conf_after: float = 0.0
    # L2 读数（几何法：指针角度 / 把手朝向 / 指示灯色）
    l2: dict | None = None
    # L2' OCR：与标定先验的对质，以及离散量的第二条通路
    cross: CrossCheck | None = None
    ocr_state: str | None = None            # 从位置指示牌读到的 ON/OFF
    ocr_value: float | None = None          # 数显表读数
    ocr_conf: float = 0.0
    # L3 非监督异常
    anomaly_score: float | None = None
    is_anomaly: bool = False
    # 观测条件
    pixel_density_px: float = 0.0
    density_target_px: float = 120.0
    quality_score: float | None = None
    aborted: bool = False

    def as_dict(self) -> dict:
        return {
            "l1": {"defect_class": self.defect_class,
                   "conf_before": round(float(self.conf_before), 4),
                   "conf_after": round(float(self.conf_after), 4)},
            "l2": self.l2,
            "l2_ocr": {"cross_check": None if self.cross is None else self.cross.as_dict(),
                       "state": self.ocr_state, "value": self.ocr_value,
                       "conf": round(float(self.ocr_conf), 4)},
            "l3": {"score": (None if self.anomaly_score is None
                             else round(float(self.anomaly_score), 4)),
                   "is_anomaly": bool(self.is_anomaly)},
            "observation": {
                "pixel_density_px": round(float(self.pixel_density_px), 3),
                "density_target_px": round(float(self.density_target_px), 3),
                "quality_score": (None if self.quality_score is None
                                  else round(float(self.quality_score), 4)),
                "aborted": bool(self.aborted)},
        }


@dataclass
class FusionResult:
    result: str
    severity: str
    needs_human_review: bool
    confidence: float
    defect_class: str | None = None
    reasons: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    def verdict(self) -> dict:
        """转成 EvidencePackage.verdict。**只含 Schema 允许的五个字段。**

        推理过程不往这里塞——verdict 是 additionalProperties: false 的，
        塞进去整份 manifest 就校验不过了（上一轮 upload_failed 就是这么
        栽的）。reasons/evidence 走 meta.jsonl。
        """
        return {"result": self.result,
                "defect_class": (None if self.result in ("UNKNOWN_ANOMALY",
                                                         "FALSE_ALARM")
                                 else self.defect_class),
                "severity": self.severity,
                "needs_human_review": bool(self.needs_human_review),
                "confidence": float(np.clip(self.confidence, 0.0, 1.0))}

    def as_dict(self) -> dict:
        d = dict(self.verdict())
        d["reasons"] = list(self.reasons)
        d["evidence"] = dict(self.evidence)
        return d


# ---------------------------------------------------------------- 置信度
#: 互证通路的四种状态。**"缺席"和"证据不足"要分开记**：前者是这一路压根没跑
#: （引擎没装、ROI 太小），后者是跑了但读到的东西不够下结论。查问题时这两种
#: 情况的处理方向完全不同——一个去看部署，一个去看变焦够不够。
CORROBORATION = ("agree", "conflict", "insufficient", "absent")


def corroboration(ev: "Evidence") -> tuple[str, str]:
    """判断第二条通路给出的是佐证、反证，还是什么都没给。返回 (状态, 说明)。

    两类目标的第二条通路不是同一件事：

    - **模拟量表计**：OCR 读表面的量程与单位，和标定先验对质
    - **开关这类离散量**：OCR 读位置指示牌，和几何法量出的把手朝向对质

    所以不能只看 `cross`——开关根本没有量程可对，`cross` 恒为 None，
    若据此判"无互证"，一整类目标的互证通路就白做了。
    """
    kind = (ev.l2 or {}).get("kind")
    if kind == "SWITCH_POSITION" and ev.ocr_state is not None \
            and (ev.l2 or {}).get("value") is not None:
        geo = canon_switch_state(ev.l2["value"])
        ocr = canon_switch_state(ev.ocr_state)
        if geo is not None and ocr is not None:
            if geo == ocr:
                return "agree", ("开关位置两路一致（几何法 %s、位置牌 %s）"
                                 % (ev.l2["value"], ev.ocr_state))
            return "conflict", ("开关位置两路不一致：几何法 %s，位置牌 %s"
                                % (ev.l2["value"], ev.ocr_state))
    c = ev.cross
    if c is None or not c.ran:
        return "absent", ("未做互证：%s" % (c.detail if c is not None
                                           else "OCR 通路未运行"))
    if c.agree is True:
        return "agree", "OCR 互证一致：%s" % c.detail
    if c.agree is False:
        return "conflict", "OCR 互证冲突：%s" % c.detail
    return "insufficient", "OCR 互证证据不足：%s" % c.detail


def _blend(ev: Evidence, corr: str, note: str, reasons: list[str]) -> float:
    """把四路证据合成一个**结论置信度**。

    注意这不是检测器的置信度，是"这个结论有多可信"。公式刻意保持单调、
    有界、可口算——评审能自己按着算一遍，这比多零点几个点的 AUC 值钱。

        base = 检测置信度；有读数时与读数置信度各占一半
        互证一致 → 把剩余不确定性砍掉 1/4
        互证冲突 → 砍半
        证据不足 → ×0.96；完全缺席 → ×0.92（少一路证据，略微保守）
    """
    base = float(np.clip(ev.conf_after, 0.0, 1.0))
    if ev.l2 is not None and ev.l2.get("value") is not None:
        rc = float(np.clip(ev.l2.get("reading_confidence", 0.0), 0.0, 1.0))
        base = 0.5 * base + 0.5 * rc
    if corr == "agree":
        base = base + (1.0 - base) * 0.25
    elif corr == "conflict":
        base *= 0.50
    elif corr == "insufficient":
        base *= 0.96
    else:
        base *= 0.92
    reasons.append(note)
    if ev.quality_score is not None and ev.quality_score < 0.5:
        base *= 0.9
        reasons.append("成像质量分 %.2f 偏低，置信度打折" % ev.quality_score)
    return float(np.clip(base, 0.0, 1.0))


def _severity_for_defect(conf: float) -> str:
    return "CRITICAL" if conf >= CRITICAL_THR else "WARN"


# ---------------------------------------------------------------- 仲裁
def fuse(ev: Evidence) -> FusionResult:
    """四路证据 → 一个结论。规则按可信度排序逐条命中，命中即返回。"""
    reasons: list[str] = []
    corr, note = corroboration(ev)
    conf = _blend(ev, corr, note, reasons)
    cls = ev.defect_class

    def done(result: str, severity: str, review: bool, why: str) -> FusionResult:
        reasons.append(why)
        return FusionResult(result=result, severity=severity,
                            needs_human_review=review, confidence=conf,
                            defect_class=cls, reasons=reasons,
                            evidence=ev.as_dict())

    # 0. 复核没走完就没有结论可言
    if ev.aborted:
        return done("INCONCLUSIVE", "INFO", True,
                    "复核中止，没有可用的复核后证据 → INCONCLUSIVE 交人")

    # 1. 互证冲突压过一切。**注意只有 conflict 才在这里返回，insufficient
    #    不算冲突**——把"没读清楚"当成"读出来不一样"，会让互证通路自己变成
    #    最大的误报源（实测复核成功率 100 % → 28.6 %）。
    if corr == "conflict":
        return done("INCONCLUSIVE", "INFO", True,
                    "第二条通路与第一条对不上，读数即使数值正常也不可信 "
                    "→ INCONCLUSIVE 交人核对标定表与航点")

    # 3. 读数类结论要求观测条件达标
    has_reading = ev.l2 is not None and ev.l2.get("value") is not None
    floor = ev.density_target_px * DENSITY_FLOOR_FRAC
    if has_reading and ev.pixel_density_px > 0.0 and ev.pixel_density_px < floor:
        return done("INCONCLUSIVE", "INFO", True,
                    "像素密度 %.0f px 未达判据线 %.0f px 的 %.0f %%，"
                    "此时谈读数精度没有意义 → 交人"
                    % (ev.pixel_density_px, ev.density_target_px,
                       DENSITY_FLOOR_FRAC * 100))

    # 4. 有读数就以读数为准，L3 的意见转成人工复核标记
    if has_reading:
        band = ev.l2.get("in_normal_band")
        if band is False:
            return done("READING_ABNORMAL", "WARN", False,
                        "读数 %s 落在正常带外 → READING_ABNORMAL"
                        % ev.l2.get("value"))
        if band is True:
            if ev.is_anomaly:
                reasons.append("L3 报异常但读数在带内，按 ICD §3.1 转人工复核，"
                               "不直接告警")
            return done("READING_OK", "INFO", bool(ev.is_anomaly),
                        "读数 %s 落在正常带内 → READING_OK" % ev.l2.get("value"))

    # 5. 没有读数时 L3 才出面
    if ev.is_anomaly:
        return done("UNKNOWN_ANOMALY", "WARN", True,
                    "无可用读数且 L3 异常分 %s 超阈 → UNKNOWN_ANOMALY 交人"
                    % ("%.3f" % ev.anomaly_score if ev.anomaly_score is not None
                       else "—"))

    # 6. 缺陷类：复核后置信度足够高就坐实
    if ev.conf_after >= CONFIRM_THR:
        return done("CONFIRMED_DEFECT", _severity_for_defect(conf), False,
                    "复核后置信度 %.2f ≥ %.2f → CONFIRMED_DEFECT"
                    % (ev.conf_after, CONFIRM_THR))

    # 7. 复核把置信度打下去了：误报被消解，这是有价值的结论而不是失败
    if ev.conf_after < ev.conf_before * FALSE_ALARM_DROP:
        return done("FALSE_ALARM", "INFO", False,
                    "复核后置信度 %.2f 跌到复核前 %.2f 的 %.0f %% 以下 "
                    "→ FALSE_ALARM（这条数据回流训练集）"
                    % (ev.conf_after, ev.conf_before, FALSE_ALARM_DROP * 100))

    # 8. 四路都没给出足以定论的证据
    return done("INCONCLUSIVE", "INFO", True,
                "四路证据都不足以定论（检测 %.2f、读数缺席、L3 未报异常）→ 交人"
                % ev.conf_after)
