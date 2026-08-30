"""表面文字的语义解析：把 OCR 的一堆字符串变成**可以拿来对质的证据**。

OCR 引擎只负责"图上有哪些字"，它不知道哪个是量程、哪个是单位、哪个是开关
位置。这一层做的就是这件事，而且**它的产物只用来和标定先验对质，不用来
替代先验**——这个分工很重要：

- 先验（量程、单位、正常带）来自标定阶段，人工录入，**精确但可能录错或错配**
- OCR 读的是**这一帧画面里这块表自己印着的东西**，**嘈杂但绝不会错配**

所以两者的价值恰好互补。对得上，说明"车确实在看标定表描述的那块表"，
几何法算出的读数才有意义；对不上，说明**要么标定表错了，要么车站错了地方**，
这时候唯一正确的动作是交给人——报一个数出去是最糟的选择，因为它看起来完全
正常，没人会去查。

这条检查抓的是一类几何法**原理上抓不到**的错误：指针角度量得再准，量程错了
读数就是错的，而且错得毫无征兆。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from patrol.perception.ocr.base import OcrLine

#: 认得的工程单位。键是归一化后的小写形式，值是 (规范写法, 物理量)。
#: 故意收得很窄——认不出来就报 None，比猜错强。
#:
#: **带上物理量这一列，是为了把两种"单位对不上"分开看。**"MPa 认成 kPa"
#: 是最常见的 OCR 错误（就差一个字母，而且 M 和 k 在小字号下形近），实测
#: 60 px 的表盘上必然发生；而"MPa 认成 A"意味着看的根本不是一块压力表。
#: 前者是噪声，后者是真事故，混为一谈会让互证通路自己变成误报源。
_UNITS = {
    "mpa": ("MPa", "pressure"), "kpa": ("kPa", "pressure"),
    "pa": ("Pa", "pressure"), "bar": ("bar", "pressure"),
    "a": ("A", "current"), "ka": ("kA", "current"), "ma": ("mA", "current"),
    "v": ("V", "voltage"), "kv": ("kV", "voltage"), "mv": ("mV", "voltage"),
    "c": ("°C", "temperature"), "℃": ("°C", "temperature"),
    "oc": ("°C", "temperature"), "%": ("%", "ratio"), "hz": ("Hz", "frequency"),
    "mm": ("mm", "length"), "cm": ("cm", "length"), "m": ("m", "length"),
    "kw": ("kW", "power"), "w": ("W", "power"),
}
#: OCR 常见的形近混淆。只在**已知应当是数字**的上下文里才做替换——
#: 无条件替换会把单位 "O" 变成 "0"，反而制造错误。
_DIGIT_FIX = {"O": "0", "o": "0", "l": "1", "I": "1", "|": "1",
              "S": "5", "B": "8", "，": ".", "。": ".", ",": "."}
_NUM_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


def _norm_unit(text: str) -> str | None:
    e = _unit_entry(text)
    return None if e is None else e[0]


def _unit_entry(text: str):
    t = str(text).strip().strip(".,:;()[]{}").replace(" ", "")
    t = t.replace("°", "").replace("º", "")
    return _UNITS.get(t.lower())


def _quantity(text: str) -> str | None:
    e = _unit_entry(text)
    return None if e is None else e[1]


def parse_number(text: str) -> float | None:
    """把一个 OCR 词条解析成数。解析不出来返回 None，**不猜**。

    要处理三类真实噪声（都是实测出来的）：尾随的多余标点（"1.6."）、
    数字与单位粘连（"1.6MPa"）、形近字符（"O" 当成 0）。
    """
    t = str(text).strip()
    if not t:
        return None
    # 数字与单位粘连："1.6MPa" → "1.6"
    m = re.match(r"^\s*([+-]?[\d.OolI|SB，。,]+)", t)
    if m is None:
        return None
    core = m.group(1)
    for bad, good in _DIGIT_FIX.items():
        core = core.replace(bad, good)
    core = core.rstrip(".")                      # "1.6." → "1.6"
    if not _NUM_RE.match(core):
        return None
    try:
        v = float(core)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


@dataclass
class DialText:
    """一块表**表面上印着的**信息。"""

    numbers: list[float] = field(default_factory=list)
    unit: str | None = None
    unit_conf: float = 0.0
    lines: int = 0
    #: 数字词条的平均识别置信度。互证要拿它决定"这批读数值不值得据以翻脸"。
    number_conf: float = 0.0

    @property
    def observed_min(self) -> float | None:
        return min(self.numbers) if self.numbers else None

    @property
    def observed_max(self) -> float | None:
        return max(self.numbers) if self.numbers else None

    def as_dict(self) -> dict:
        return {"numbers": [round(float(v), 4) for v in self.numbers],
                "unit": self.unit, "unit_conf": round(float(self.unit_conf), 3),
                "number_conf": round(float(self.number_conf), 3),
                "lines": int(self.lines)}


def parse_dial_text(lines: list[OcrLine]) -> DialText:
    """把一块表上的 OCR 结果分成"数字"和"单位"两堆。"""
    nums: list[tuple[float, float]] = []
    unit, uconf = None, 0.0
    for ln in lines or []:
        u = _norm_unit(ln.text)
        if u is not None and not _NUM_RE.match(ln.text.strip()):
            if ln.conf > uconf:
                unit, uconf = u, float(ln.conf)
            continue
        v = parse_number(ln.text)
        if v is not None:
            nums.append((v, float(ln.conf)))
    nums.sort()
    mean_conf = (sum(c for _, c in nums) / len(nums)) if nums else 0.0
    return DialText(numbers=[v for v, _ in nums], unit=unit, unit_conf=uconf,
                    lines=len(lines or []), number_conf=mean_conf)


@dataclass
class CrossCheck:
    """一次对质的结果。**每一项都要能说清"凭什么"**，所以 detail 是必填的。"""

    ran: bool                       # 是否真的做了对质（OCR 有没有读到东西）
    agree: bool | None              # True 一致 / False 冲突 / None 证据不足
    scale_hits: int = 0             # 落在先验刻度上的数字个数
    scale_total: int = 0
    unit_agree: bool | None = None
    detail: str = ""

    def as_dict(self) -> dict:
        return {"ran": bool(self.ran),
                "agree": self.agree,
                "scale_hits": int(self.scale_hits),
                "scale_total": int(self.scale_total),
                "unit_agree": self.unit_agree,
                "detail": self.detail}


#: 数字落在量程区间外多远才算越界，取量程跨度的比例。OCR 把 "1.6" 读成
#: "1.8" 这类小噪声不该判冲突，而看错表带来的偏差是成倍的。
_RANGE_TOL_FRAC = 0.12
#: 观测到的最大标签离 range_max 差得超过跨度的这个比例，判为先验量程偏大。
_SPAN_SHORT_FRAC = 0.35
#: 附带统计用：数字落在"等分标签网格"上的容差。**只作为证据展示，不参与判定**
#: ——真表盘每隔几分之一量程标一个数各家不同，拿它当判据会误伤。
_GRID_TOL_FRAC = 0.06

# ---- 判"冲突"的举证门槛 ----
#
# **翻脸是有代价的，所以举证责任不对称。**判一次"标定错配"意味着：这次复核
# 白跑（预算 −1）、结论变成 INCONCLUSIVE、一条工单推到人面前。而漏判的代价
# 只是"少了一路佐证"——读数通路照常给结论，置信度低一档。
#
# 实测的教训：一块 120 px 的表，OCR 会把 0.4 读成 4、把 1.2 读成 12（**小数点
# 是第一个丢掉的东西**），单个越界数字满地都是。按"有一个数越界就翻脸"的写法，
# 一轮巡检里 5 个证据包有 3 个被自己的互证通路判成错配，复核成功率从 100 %
# 掉到 28.6 %——互证通路自己变成了最大的误报源。
#
# 所以：证据不足时报 None（证据不足），**不报 False（冲突）**。三者是三件事。
_MIN_NUMS_FOR_CONFLICT = 3      # 至少读到这么多个数字，才谈得上"量程对不上"
_CONFLICT_MAJORITY = 0.60       # 越界的得占多数，个别离群值是噪声不是证据
_MIN_NUM_CONF = 0.70            # 数字识别置信度低于此，这批读数不作为翻脸依据
_MIN_UNIT_CONF = 0.80           # 同理，单位


#: 小数点丢失的检验倍率。**只试 10^±1、10^±2，不试 10^±3。**
#: 千倍正好是 MPa↔kPa 这类单位前缀之差，那是真的看错了表或标定错配，
#: 不该被"小数点丢了"这个借口盖过去。
_SCALE_HYPOTHESES = (0.01, 0.1, 10.0, 100.0)


def _fits_scale(numbers: list[float], lo: float, hi: float, n_labels: int,
                k: float) -> bool:
    """整批数字乘上 k 之后，是不是正好落在先验刻度网格上、且够得着上限。

    判据故意收得很紧（**全部**落在网格上，不是多数），因为这条通路是用来
    豁免冲突判定的——放松它等于把互证通路关掉。反过来说，三个数一起除以 10
    就同时对上 0 / 1.2 / 1.6 三个刻度，这种巧合不可能是偶然。
    """
    span = abs(hi - lo)
    if span <= 1e-9 or not numbers:
        return False
    n = max(2, int(n_labels))
    grid = [lo + (hi - lo) * j / (n - 1) for j in range(n)]
    tol = span * _GRID_TOL_FRAC
    scaled = [v * k for v in numbers]
    matched = set()
    for v in scaled:
        near = [j for j, t in enumerate(grid) if abs(v - t) <= tol]
        if not near:
            return False                     # 有一个落不上，整套假设作废
        matched.add(near[0])
    # **必须落在至少两个不同的刻度上。**只要求"每个数都能找到一个刻度"是
    # 不够的：把一批数缩小 100 倍之后它们会全都挤到零刻度附近，于是任何
    # 一批噪声都能"通过"。实测 [0.8, 16] ×0.1 = [0.08, 1.6] 就是这么蒙混
    # 过关的——0.08 落在零刻度的容差里。
    if len(matched) < 2:
        return False
    return max(scaled) >= max(lo, hi) - span * _SPAN_SHORT_FRAC


def _decimal_dropout(numbers: list[float], lo, hi, n_labels: int) -> float | None:
    """检出"小数点被 OCR 吃掉"这一类系统性误读，返回补回来的倍率。

    **这是实测出来的头号失效模式，不是假想的。**一块 120 px 的表，
    RapidOCR 会把 0.4 读成 4、1.2 读成 12、1.6 读成 16——小数点是第一个
    丢掉的东西。按字面值判，这批数字"全部超出 0–1.6 的量程"，互证通路会
    一口咬定标定错配；而实际上把它们统一除以 10，三个数同时精确落回
    0 / 1.2 / 1.6 三个刻度上。

    区分噪声与真事故的关键正是**整批一致**：真看错了表（0–6 的表配 0–1.6
    的先验），读到的 1.5 / 3 / 4.5 除以任何一个倍率都凑不齐一整套刻度。
    """
    # 少于三个数不做这个豁免：样本太少时"整批一致"没有说服力，而豁免的
    # 代价是把一次真冲突放过去。
    if lo is None or hi is None or len(numbers) < 3:
        return None
    for k in _SCALE_HYPOTHESES:
        if _fits_scale(numbers, float(lo), float(hi), n_labels, k):
            return k
    return None


#: 判"这批数字自成一套刻度"时，允许的最大格数。真表盘的标签是均匀铺开的，
#: 五个标签就是四格；给到两倍余量容得下漏读，但挡得住"随便几个数都能凑出
#: 一个公差"的退化情形。
_MAX_LATTICE_STEPS = 2


def implied_range(numbers: list[float], n_labels: int = 5
                  ) -> tuple[float, float] | None:
    """这批数字自己能不能构成一套自洽的刻度？能的话返回它隐含的量程。

    **这条是"判冲突"的举证门槛，也是整个互证通路最关键的一处设计。**

    举证责任的方向必须是"证明这块表是**别的**表"，而不是"证明这些数字对不上
    先验"。两者听起来相近，代价却完全不同：后者把每一次 OCR 噪声都变成一次
    误判——实测一块 120 px 的表会读出 [0.8, 2.0, 2.4, 10.0] 这种半对半错的
    东西，按"对不上就翻脸"判，好表被判成标定错配，复核预算白烧一次。

    自洽的判据有两条，都来自"表盘上的标签是均匀铺开的"这一个事实：

    1. 所有相邻间隔都是最小间隔的整数倍（落在同一个公差网格上）
    2. 总跨度不超过 `_MAX_LATTICE_STEPS × (标签数−1)` 格——挡住退化解，
       否则任取几个数，让最小间隔去整除其余间隔，几乎总能凑出一个网格

    代入实测数据：真看错表读到的 [0, 1.5, 3, 4.5, 6] 公差 1.5、共 4 格，
    自洽，隐含量程 0–6，与先验 0–1.6 冲突——这是真事故，该报。
    而噪声 [0.8, 2.0, 2.4, 10.0] 最小间隔 0.4、跨 23 格，远超上限，
    判为"读不清楚"而不是"看错了表"。
    """
    xs = sorted({round(float(v), 6) for v in numbers})
    if len(xs) < 3:
        return None
    diffs = [b - a for a, b in zip(xs, xs[1:])]
    step = min(d for d in diffs if d > 1e-9) if any(d > 1e-9 for d in diffs) else 0.0
    if step <= 1e-9:
        return None
    for d in diffs:
        r = d / step
        if abs(r - round(r)) > 0.12:            # 不落在同一个公差网格上
            return None
    span_steps = (xs[-1] - xs[0]) / step
    if span_steps > _MAX_LATTICE_STEPS * max(1, int(n_labels) - 1):
        return None
    return xs[0], xs[-1]


def cross_check_dial(dial: DialText, priors: dict | None,
                     *, n_labels: int = 5) -> CrossCheck:
    """把表面读到的数字与单位，和标定先验对质。

    判据要**与表盘设计无关**，否则换一款表就得改代码。所以用两条都只依赖
    "表盘会标出自己的量程"这一个事实的检查：

    1. **读到的数字都得落在先验量程内**（留 12 % 容差）。抓的是先验量程
       偏小：先验说 0–1.6，实际是 0–6 的表，读到的 3 / 4.5 / 6 立刻越界。
    2. **读到 3 个以上标签时，最大的那个得够得着 range_max**。抓的是先验
       量程偏大：先验说 0–6，实际是 0–1.6 的表，标签最大只到 1.6，够不着。

    一开始写的是"数字必须落在 range_min + k/4·span 的网格上"，那条依赖
    "表盘每 1/4 量程标一个数"这个只对本项目渲染器成立的假设，换成真表
    （每 1/10 标一个）会整块判冲突。网格命中数仍然算出来，但只作为证据
    展示，不参与判定。

    单位对不上分两种，见 _UNITS 的说明：同一物理量的前缀混淆（MPa/kPa）是
    OCR 噪声，只压低置信度；跨物理量（MPa/A）才是真冲突。
    """
    if priors is None:
        return CrossCheck(ran=False, agree=None, detail="无标定先验，无从对质")
    if not dial.numbers and dial.unit is None:
        return CrossCheck(ran=False, agree=None,
                          detail="OCR 未读到任何文字（多半是还没放大到位）")

    lo, hi = priors.get("range_min"), priors.get("range_max")
    hits = total = out_of_range = 0
    span_short = False
    if lo is not None and hi is not None and dial.numbers:
        lo, hi = float(lo), float(hi)
        span = abs(hi - lo)
        if span > 1e-9:
            rtol = span * _RANGE_TOL_FRAC
            n = max(2, int(n_labels))
            grid = [lo + (hi - lo) * k / (n - 1) for k in range(n)]
            gtol = span * _GRID_TOL_FRAC
            for v in dial.numbers:
                total += 1
                if v < min(lo, hi) - rtol or v > max(lo, hi) + rtol:
                    out_of_range += 1
                if any(abs(v - t) <= gtol for t in grid):
                    hits += 1
            if total >= _MIN_NUMS_FOR_CONFLICT:
                span_short = (max(dial.numbers)
                              < max(lo, hi) - span * _SPAN_SHORT_FRAC)

    want_unit = priors.get("unit")
    unit_agree: bool | None = None
    same_quantity: bool | None = None
    if dial.unit is not None and want_unit:
        unit_agree = (_norm_unit(str(want_unit)) or str(want_unit)) == dial.unit
        qa, qb = _quantity(dial.unit), _quantity(str(want_unit))
        same_quantity = None if (qa is None or qb is None) else (qa == qb)

    bits = []
    if total:
        bits.append("读到 %d 个刻度数字 %s（均置信 %.2f），先验量程 [%g, %g]"
                    % (total, dial.numbers, dial.number_conf, lo, hi))
    if unit_agree is not None:
        bits.append("单位 %s %s 先验 %s"
                    % (dial.unit, "对上" if unit_agree else "对不上", want_unit))
    why = "；".join(bits)

    def cc(agree, tail):
        return CrossCheck(True, agree, hits, total, unit_agree,
                          (why + " → " + tail) if why else tail)

    # --- 硬冲突：单位跨了物理量。一块压力表不可能标成 A ---
    if unit_agree is False and same_quantity is False \
            and dial.unit_conf >= _MIN_UNIT_CONF:
        return cc(False, "单位跨物理量，看的根本不是这块表 → 冲突")
    # 同物理量的前缀混淆（MPa↔kPa）：不翻脸，但也不算佐证
    if unit_agree is False:
        return cc(None, "单位仅前缀不同，判为 OCR 噪声，本次不作为佐证")

    # --- 先排除"小数点被吃掉"这类系统性误读 ---
    if out_of_range or span_short:
        k = _decimal_dropout(dial.numbers, lo, hi, n_labels)
        if k is not None:
            scaled = [round(v * k, 6) for v in dial.numbers]
            return cc(True, "整批数字 ×%g 后精确落回先验刻度 %s，"
                            "判为 OCR 丢了小数点而非标定错配 → 一致"
                      % (k, scaled))

    # --- 量程冲突：必须能指认出"它其实是哪一块表"，见 implied_range ---
    strong = (total >= _MIN_NUMS_FOR_CONFLICT
              and dial.number_conf >= _MIN_NUM_CONF)
    if (out_of_range or span_short) and strong:
        got = implied_range(dial.numbers, n_labels)
        if got is not None and lo is not None and hi is not None:
            span = abs(float(hi) - float(lo))
            far = (abs(got[0] - float(lo)) > span * _RANGE_TOL_FRAC
                   or abs(got[1] - float(hi)) > span * _RANGE_TOL_FRAC)
            if far:
                return cc(False, "这批数字自成一套刻度，隐含量程 [%g, %g]，"
                                 "与先验 [%g, %g] 不是同一块表 → 冲突"
                          % (got[0], got[1], lo, hi))
    if out_of_range or span_short:
        return cc(None, "有 %d 个数字对不上，但它们凑不出一套自洽的刻度"
                        "（样本 %d 个、均置信 %.2f）→ 判为读不清楚，证据不足"
                  % (out_of_range, total, dial.number_conf))

    # --- 一致 ---
    if total >= 2 and dial.number_conf >= _MIN_NUM_CONF:
        return cc(True, "全部落在先验量程内 → 一致")
    if unit_agree is True and dial.unit_conf >= _MIN_UNIT_CONF:
        return cc(True, "仅单位可对质，一致")
    return cc(None, "读到的文字不足以对质")


# ------------------------------------------------------------------ 离散量
#: 开关位置指示牌的词表。几何法量把手朝向，这里读牌子上的字，两条通路独立。
#: 开关位置的规范词表。**两条通路说的是同一件事，用的却是两套词。**
#:
#: 几何法量把手朝向，报的是 CLOSED / OPEN（合闸 / 分闸）；OCR 读位置指示牌，
#: 牌子上印的是 ON / OFF。第一版直接拿两个字符串比对，于是"几何法 CLOSED
#: vs 位置牌 ON"——两路其实完全一致——被判成互相矛盾，一轮里 6 个开关证据包
#: 全部误判为需要人工复核。归一到同一套词是必须的，比对之前先过这张表。
_SWITCH_WORDS = {
    "on": "ON", "close": "ON", "closed": "ON", "合": "ON", "合闸": "ON",
    "1": "ON", "0n": "ON", "оn": "ON",
    "off": "OFF", "open": "OFF", "opened": "OFF", "分": "OFF", "分闸": "OFF",
    "0ff": "OFF", "of": "OFF",
}


def canon_switch_state(text) -> str | None:
    """把任意一路给出的开关状态归一成 ON / OFF。认不出来返回 None。"""
    if text is None:
        return None
    return _SWITCH_WORDS.get(str(text).strip().strip(".:").lower())


def read_switch_text(lines: list[OcrLine]) -> tuple[str | None, float]:
    """从位置指示牌读开关状态。返回 (ON/OFF/None, 置信度)。"""
    best: tuple[str, float] | None = None
    for ln in lines or []:
        w = canon_switch_state(ln.text)
        if w is not None and (best is None or ln.conf > best[1]):
            best = (w, float(ln.conf))
    return best if best is not None else (None, 0.0)


def read_digital_value(lines: list[OcrLine]) -> tuple[float | None, float]:
    """数显表读数：取**字号最大**的那个数字。

    数显屏上除了读数还常印着单位和型号小字，按置信度挑会挑到印刷体的型号
    （印刷体比七段码好认得多）。按字高挑才对——读数永远是屏上最大的那个。
    """
    best: tuple[float, float, float] | None = None      # (高度, 值, 置信度)
    for ln in lines or []:
        v = parse_number(ln.text)
        if v is None:
            continue
        h = abs(ln.bbox[3] - ln.bbox[1])
        if best is None or h > best[0]:
            best = (h, v, float(ln.conf))
    return (best[1], best[2]) if best is not None else (None, 0.0)
