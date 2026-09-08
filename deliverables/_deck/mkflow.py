# -*- coding: utf-8 -*-
"""画小车的运行逻辑流程图（SVG）。

不是进程和接口，是车从出发到回到巡航这一趟里，每一步在判断什么、
判断不过又往哪走。L1 至 L4 四个识别环节按它们在流程中的位置摆。
"""
import io
import os

W, H = 1600, 1050
NAVY, BLUE, RED, GREEN, AMBER = "#0B3C6B", "#1667B0", "#C0202B", "#1E7A46", "#D98200"
TEXT, MUTED, EDGE = "#1A2430", "#5D6E7E", "#D5E1EC"
AMB_S, BLU_S, GRN_S, RED_S, GREY_S = "#FBEBD2", "#E4EEF7", "#DCEFE4", "#FBE3E4", "#F4F7FA"
FONT = "'Microsoft YaHei','PingFang SC','Noto Sans CJK SC','WenQuanYi Zen Hei',sans-serif"
o = []


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rect(x, y, w, h, fill="#fff", stroke=None, sw=2, rx=8, dash=None):
    d = ' stroke-dasharray="%s"' % dash if dash else ""
    st = ' stroke="%s" stroke-width="%s"' % (stroke, sw) if stroke else ""
    o.append('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="%s"%s%s/>'
             % (x, y, w, h, rx, fill, st, d))


def txt(x, y, s, size=15, fill=TEXT, anchor="start", weight="normal", ls=None):
    """写一行或多行；多行按 ls 的行距往下排。"""
    lines = s.split("\n")
    step = ls or size * 1.45
    for i, ln in enumerate(lines):
        o.append('<text x="%g" y="%g" font-family="%s" font-size="%g" fill="%s" '
                 'text-anchor="%s" font-weight="%s">%s</text>'
                 % (x, y + i * step, FONT, size, fill, anchor, weight, esc(ln)))


def arrow(pts, color=MUTED, sw=2.6, dash=None):
    d = ' stroke-dasharray="%s"' % dash if dash else ""
    p = " ".join("%g,%g" % q for q in pts)
    o.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="%g" '
             'marker-end="url(#a-%s)" stroke-linejoin="round"%s/>'
             % (p, color, sw, color.lstrip("#"), d))


MARKERS = set()


def mk(color):
    MARKERS.add(color)
    return color


# ---------- 三种节点 ----------
def act(x, y, w, h, title, body, col=BLUE):
    """动作节点：白底，左侧色条"""
    rect(x, y, w, h, "#fff", EDGE, 1.6, 8)
    o.append('<rect x="%g" y="%g" width="7" height="%g" rx="3" fill="%s"/>' % (x, y, h, col))
    txt(x + 22, y + 34, title, 20, col, weight="bold")
    txt(x + 22, y + 62, body, 15, MUTED, ls=22)


def dec(x, y, w, h, title, body):
    """判断节点：琥珀底，标题前加菱形记号"""
    rect(x, y, w, h, AMB_S, AMBER, 2, 8)
    o.append('<path d="M %g %g l 9 -9 l 9 9 l -9 9 Z" fill="%s"/>' % (x + 20, y + 27, AMBER))
    txt(x + 48, y + 34, title, 19, "#8A5200", weight="bold")
    txt(x + 22, y + 62, body, 14.5, "#70430A", ls=21)


def lnode(x, y, w, h, tag, name, body, col):
    """识别环节：顶部色条写 L 编号与名字"""
    rect(x, y, w, h, "#fff", col, 2.2, 8)
    o.append('<path d="M %g %g h %g a8,8 0 0 1 8,8 v30 h-%g v-30 a8,8 0 0 1 8,-8 Z" fill="%s"/>'
             % (x + 8, y, w - 16, w, col))
    txt(x + w / 2, y + 27, tag + "　" + name, 18, "#fff", anchor="middle", weight="bold")
    txt(x + 16, y + 66, body, 14.5, TEXT, ls=21)


def band(x, y, w, h, label, col, fill):
    rect(x, y, w, h, fill, col, 1.4, 12, dash="7 5")
    rect(x + 18, y - 15, 15 + len(label) * 15, 30, col, None, 0, 15)
    txt(x + 26, y + 6, label, 17, "#fff", weight="bold")


def lab(x, y, s, col=MUTED, size=14, anchor="start", weight="normal"):
    txt(x, y, s, size, col, anchor, weight)


# ================= 标题 =================
txt(40, 44, "无人车主动式 AI 巡检　运行逻辑", 30, NAVY, weight="bold")
txt(500, 44, "从出发到回到巡航的一趟，每一步在判断什么，判断不过往哪走", 17, MUTED)

# ================= 巡航态 =================
band(30, 92, 1540, 214, "巡航态　CRUISE　30 Hz　只做检出，不做测量", BLUE, BLU_S)
NY, NH = 122, 158
xs = [46, 302, 558, 814, 1070, 1326]
NW = 216
lnode(xs[0], NY, NW, NH, "L1", "目标检测", "全图扫描 30 Hz\n巡航级阈值 0.25\n宁可多报，不漏检", BLUE)
dec(xs[1], NY, NW, NH, "检出可疑？", "置信度落在\n0.25 至 0.60 的\n可疑带内")
dec(xs[2], NY, NW, NH, "连续三帧？", "同一 track_id\n连续三帧才确认\n防单帧抖动")
dec(xs[3], NY, NW, NH, "抑制放行？", "该航点未复核过\n定位有效\n不在恢复静默期")
dec(xs[4], NY, NW, NH, "预算还够？", "N_max =\n⌊(T_max − L/v) / T_r⌋")
act(xs[5], NY, NW, NH, "停车", "下发「暂停巡检」\n网关五项校验\n校验不过即拒绝", RED)
for i in range(5):
    x0 = xs[i] + NW
    arrow([(x0 + 4, NY + NH / 2), (xs[i + 1] - 6, NY + NH / 2)], mk(NAVY))
    if i:
        lab(x0 + 20, NY + NH / 2 - 12, "是", GREEN, 15, weight="bold")

# 四个判断的「否」并到同一条回流轨
RAIL = 292
for i in (1, 2, 3, 4):
    cx = xs[i] + NW / 2
    arrow([(cx, NY + NH + 4), (cx, RAIL)], mk(RED), 2.2)
    lab(cx + 8, NY + NH + 24, "否", RED, 15, weight="bold")
o.append('<polyline points="1178,292 120,292 120,%g" fill="none" stroke="%s" stroke-width="2.6" '
         'marker-end="url(#a-%s)"/>' % (NY + NH + 6, RED, RED.lstrip("#")))
lab(650, 326, "四个判断只要有一个不过就不复核，继续巡航；其中超出预算的目标顺延到下一轮再看",
    RED, 15.5, "middle", "bold")

# ================= 复核态 =================
band(30, 360, 1540, 502, "复核态　VERIFY　停下来才做测量", RED, "#FBFCFD")

# 对准与变焦
act(46, 392, 300, 118, "对准　AIM", "针孔几何前馈算 aim_offset\n再用 PID 闭合像素残差", RED)
dec(386, 392, 320, 118, "像素密度 ≥ 96 px？", "读数门槛 = 120 × 0.8\n达不到就读不出所需精度")
act(746, 392, 260, 118, "变焦　ZOOM", "按目标当前密度算倍率\n变焦重拍，回到上一个判断", RED)
arrow([(350, 451), (380, 451)], mk(NAVY))
arrow([(710, 451), (740, 451)], mk(RED))
lab(716, 438, "否", RED, 15, weight="bold")
arrow([(876, 388), (876, 374), (546, 374), (546, 388)], mk(RED), 2.2)
rect(1046, 392, 514, 118, GREY_S, EDGE, 1.4, 8)
lab(1066, 424, "变焦到光学上限仍不足怎么办", NAVY, 16, weight="bold")
txt(1066, 452, "不给一个数值精确但实际不可信的读数，\n直接出「证据不足 INCONCLUSIVE」，交人工复核。", 14.5, MUTED, ls=22)

# 四路并行判读
arrow([(546, 514), (546, 552)], mk(GREEN))
lab(556, 540, "是", GREEN, 15, weight="bold")
rect(46, 556, 1012, 236, "#fff", EDGE, 1.6, 10)
lab(66, 584, "四路并行判读　同一张放大后的图，四个模型各答一个子问题", NAVY, 16, weight="bold")
LW, LY, LH = 235, 598, 176
for i, (tag, name, body, col) in enumerate([
        ("L1", "复核级检测", "阈值提到 0.60\n在放大后的画面重判\n输出 Δconf 度量增益", BLUE),
        ("L2", "分割 + 几何", "指针与刻度分割\n拟合圆心与角度\n换算成读数", NAVY),
        ("L2′", "OCR", "铭牌量程与单位\n位置指示牌文字\n输出字符串", "#6B4E9B"),
        ("L3", "非监督异常", "只用正常样本训练\n对训练集外的异常打分\n承接无标注的外观缺陷", GREEN)]):
    lnode(58 + i * (LW + 16), LY, LW, LH, tag, name, body, col)

# L4 仲裁
arrow([(1062, 674), (1096, 674)], mk(NAVY))
rect(1100, 556, 460, 236, "#fff", NAVY, 2.4, 10)
o.append('<path d="M 1108 556 h 444 a8,8 0 0 1 8,8 v34 h-460 v-34 a8,8 0 0 1 8,-8 Z" fill="%s"/>' % NAVY)
txt(1330, 584, "L4　显式规则仲裁", 19, "#fff", anchor="middle", weight="bold")
txt(1122, 628, "四路证据矛盾时按写死的规则取舍，\n不用模型。每条结论附 reasons 字段，\n可逐级追溯到是哪一路、哪个数触发的。", 15, TEXT, ls=23)
rect(1122, 706, 416, 68, BLU_S, None, 0, 8)
txt(1138, 732, "像素密度不达标 · 四路互相矛盾 · 置信度不足\n三种情形都走「证据不足」，交人工。", 14, NAVY, ls=20)

# 六种结论
VY = 806
arrow([(1330, 792), (1330, VY - 4)], mk(NAVY), 2.2)
lab(56, VY + 24, "仲裁输出　六种结论", NAVY, 16, weight="bold")
verdicts = [("确认缺陷", "CONFIRMED_DEFECT", RED), ("读数越界", "READING_ABNORMAL", RED),
            ("读数正常", "READING_OK", GREEN), ("误报消解", "FALSE_ALARM", BLUE),
            ("未知异常", "UNKNOWN_ANOMALY", AMBER), ("证据不足", "INCONCLUSIVE", MUTED)]
VW = 208
for i, (cn, en, col) in enumerate(verdicts):
    x = 240 + i * (VW + 12)
    rect(x, VY, VW, 36, "#fff", col, 1.6, 18)
    o.append('<circle cx="%g" cy="%g" r="5.5" fill="%s"/>' % (x + 15, VY + 18, col))
    lab(x + 26, VY + 23, cn, col, 14.5, weight="bold")
    lab(x + 90, VY + 22, en, MUTED, 9.5)

# ================= 收尾 =================
CY = 884
act(46, CY, 300, 106, "打包证据　PACK", "before / after 配对\n合并 sidecar，生成 manifest", NAVY)
dec(386, CY, 280, 106, "网络可用？", "断网是常态，\n不能因此丢证据")
act(706, CY, 380, 106, "上传　·　落盘缓存", "可用则直传云端入库；\n断网则落盘，恢复后断点续传", NAVY)
act(1126, CY, 434, 106, "恢复路线　RESUME", "写入抑制表并设静默期，回到巡航态\n避免刚起步又被同一目标触发", BLUE)
arrow([(350, CY + 53), (380, CY + 53)], mk(NAVY))
arrow([(670, CY + 53), (700, CY + 53)], mk(NAVY))
arrow([(1090, CY + 53), (1120, CY + 53)], mk(NAVY))
arrow([(196, 846), (196, CY - 4)], mk(NAVY), 2.2)
def hook(cx, cy, note=None):
    o.append('<circle cx="%g" cy="%g" r="19" fill="#fff" stroke="%s" stroke-width="2.4"/>' % (cx, cy, BLUE))
    lab(cx, cy + 8, "⟲", BLUE, 22, "middle", "bold")
    if note:
        lab(cx - 26, cy + 6, note, BLUE, 14, "end", "bold")
hook(20, NY + NH / 2)
hook(1580, CY + 53, "回到巡航态")

# ================= 贯穿全程的安全抢占 =================
rect(30, 1004, 1540, 40, RED_S, RED, 2, 10)
o.append('<path d="M 52 1016 l 10 -11 l 10 11 l -10 11 Z" fill="%s"/>' % RED)
lab(84, 1030, "贯穿全程", RED, 16, weight="bold")
lab(174, 1030, "安全事件（急停 / 避障 / 限速）随时抢占：200 ms 内中止正在进行的复核，回到安全状态，"
                "再由网关下发「恢复路线」。AI 只发高层指令，转向、扭矩与制动力始终由车辆控制层自己管。", TEXT, 14.5)

defs = "".join(
    '<marker id="a-%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" markerHeight="6.5" '
    'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="%s"/></marker>' % (c.lstrip("#"), c)
    for c in MARKERS | {NAVY, RED, GREEN, BLUE, MUTED})

svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">'
       '<defs>%s</defs><rect width="%d" height="%d" fill="#fff"/>%s</svg>'
       % (W, H, W, H, defs, W, H, "".join(o)))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "运行逻辑流程图.svg")
io.open(OUT, "w", encoding="utf-8").write(svg)
print("写出 %s，%d 字节" % (os.path.basename(OUT), len(svg)))
