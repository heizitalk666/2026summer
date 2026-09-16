"""最终答辩 PPT。

stage A  用 pptx skill 的 add_slide.py 从 C424 模板复制出 25 页骨架
stage B  python-pptx：模板页留页眉页脚，正文全部用原生形状重写

    python final.py        两段都跑
    python final.py b      只跑 stage B（骨架已存在时）
"""
import copy, io, os, re, shutil, subprocess, sys
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt
from PIL import Image as PILImage

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DLV = os.path.join(REPO, 'deliverables')
IMG = os.path.join(HERE, 'img')
# stage A 要用 pptx skill 的 unpack / add_slide / pack，每台机器装的位置不同，用环境变量指
SK = os.environ.get('PPTX_SKILL', os.path.expanduser('~/.claude/skills/pptx/scripts'))
TEMPLATE = os.environ.get('C424_TEMPLATE', os.path.join(REPO, 'C424 PPT模板-2024新.pptx'))
SKELETON = os.path.join(HERE, 'skeleton_final.pptx')   # stage A 的产物，30 MB，不进版本库
OUT = os.path.join(DLV, '最终答辩.pptx')
E = 914400

# ------------------------------------------------------------------ 版式常量
W, H, M = 13.333, 7.5, 0.42
BODY_R = W - M                      # 12.913
FONT = "微软雅黑"
MONO = "Consolas"
NAVY, BLUE, BLUE_SOFT = "0B3C6B", "1667B0", "E4EEF7"
CARD, EDGE, TEXT, MUTED = "F4F7FA", "D5E1EC", "1A2430", "5D6E7E"
RED, RED_SOFT = "C0202B", "FBE3E4"
GREEN, GREEN_SOFT = "1E7A46", "DCEFE4"
AMBER, AMBER_SOFT = "B86E00", "FBEBD2"
WHITE = "FFFFFF"
PILL_PT, LABEL_PT = 20, 28

TITLE = "基于 RK3576 边缘计算的无人车主动式 AI 巡检系统设计"
SUBTITLE = "测控系统综合实训　·　实训地点 A210　·　指导教师 陈震"
REPORTER, DATE = "汇报人：吴明哲", "2026 年 9 月"
SECTIONS = ["研究背景与任务要求", "总体方案与技术路线", "关键技术实现", "测试结果与指标达成", "总结与展望"]

# 模板第几页 → 类型
MAP = [(1, "cover"), (2, "toc"), (13, "c"), (13, "c"), (5, "toc"),
       (13, "c"), (13, "c"), (13, "c"), (13, "c"), (13, "c"), (8, "toc"),
       (13, "c"), (13, "c"), (13, "c"), (13, "c"), (13, "c"), (13, "c"), (12, "toc"),
       (13, "c"), (13, "c"), (13, "c"), (15, "toc"), (13, "c"), (13, "c"), (13, "c"), (19, "end")]
assert len(MAP) == 26


def run(args):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if r.returncode != 0:
        raise SystemExit("命令失败：%s\n%s\n%s" % (" ".join(args), r.stdout[-3000:], r.stderr[-3000:]))
    return r.stdout


# ------------------------------------------------------------------ stage A
def stage_a():
    U = os.path.join(HERE, "u_final")
    shutil.rmtree(U, ignore_errors=True)
    run([sys.executable, os.path.join(SK, "office", "unpack.py"), TEMPLATE, U])
    pres_path = os.path.join(U, "ppt", "presentation.xml")
    rels = open(os.path.join(U, "ppt", "_rels", "presentation.xml.rels"), encoding="utf-8").read()
    rid_to_target = {}
    for m in re.finditer(r"<Relationship\b[^>]*/>", rels):
        rid = re.search(r'Id="(rId\d+)"', m.group(0))
        tgt = re.search(r'Target="slides/(slide\d+\.xml)"', m.group(0))
        if rid and tgt:
            rid_to_target[rid.group(1)] = tgt.group(1)
    pres = open(pres_path, encoding="utf-8").read()
    tpl_files = [rid_to_target[r] for r in re.findall(r'<p:sldId\b[^>]*r:id="(rId\d+)"', pres)]
    entries = []
    for tpl_idx, _ in MAP:
        out = run([sys.executable, os.path.join(SK, "add_slide.py"), U, tpl_files[tpl_idx - 1]])
        entry = re.search(r'<p:sldId id="\d+" r:id="rId\d+"/>', out).group(0)
        pres = open(pres_path, encoding="utf-8").read().replace("</p:sldIdLst>", entry + "</p:sldIdLst>")
        open(pres_path, "w", encoding="utf-8").write(pres)
        entries.append(entry)
    pres = open(pres_path, encoding="utf-8").read()
    pres = re.sub(r"<p:sldIdLst>.*?</p:sldIdLst>", "<p:sldIdLst>" + "".join(entries) + "</p:sldIdLst>", pres, flags=re.S)
    open(pres_path, "w", encoding="utf-8").write(pres)
    run([sys.executable, os.path.join(SK, "clean.py"), U])
    print(run([sys.executable, os.path.join(SK, "office", "pack.py"), U, SKELETON, "--original", TEMPLATE]).strip()[-400:])


# ------------------------------------------------------------------ 底层工具
def I(v):
    return Emu(int(round(v * E)))


_UNIT = re.compile(r"(\d) (%|px|ms|m/s|MB|fps|Hz|MPa|s|m|倍|次|条|项|张|帧|轮|个|位)(?![A-Za-z])")
_BRACKET = re.compile(r"(\[[\d.]+,) ([\d.]+\])")


def glue(t):
    """数字和单位之间用不断行空格，避免「5 / %」「% / FS」这种跨行拆开。"""
    t = _UNIT.sub("\\1\u00a0\\2", t)
    t = t.replace("% FS", "%\u00a0FS")
    return _BRACKET.sub("\\1\u00a0\\2", t)


def emw(s):
    w = 0.0
    for ch in s:
        if ch in " \u00a0":
            w += 0.28
        elif ord(ch) < 128:
            w += 0.3 if ch in "il.,:;|'!()[]" else 0.56
        else:
            w += 1.0
    return w


def _font(run, size, color=TEXT, bold=False, face=FONT, latin=None):
    f = run.font
    f.size = Pt(size)
    f.bold = bool(bold)
    f.color.rgb = RGBColor.from_string(color)
    rPr = run._r.get_or_add_rPr()
    rPr.set("lang", "zh-CN")
    for tag, tf in (("a:latin", latin or face), ("a:ea", face), ("a:cs", latin or face)):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", tf)


def _bullet(para, color=RED, indent=0.2):
    pPr = para._p.get_or_add_pPr()
    pPr.set("marL", str(int(indent * E)))
    pPr.set("indent", str(-int(indent * E)))
    for tag in ("a:buClr", "a:buFont", "a:buChar", "a:buNone"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    buClr = pPr.makeelement(qn("a:buClr"), {})
    buClr.append(buClr.makeelement(qn("a:srgbClr"), {"val": color}))
    pPr.append(buClr)
    pPr.append(pPr.makeelement(qn("a:buFont"), {"typeface": "Arial"}))
    pPr.append(pPr.makeelement(qn("a:buChar"), {"char": "▪"}))


ALIGN = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}
ANCHOR = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}


def fill_frame(tf, content, size=12, color=TEXT, bold=False, align="l", spacing=1.15, gap=0, face=FONT, latin=None):
    """content: str | [段]；段: str | [片段] | dict(runs=, bullet=, gap=, align=, size=)；片段: str | (str, opts)"""
    paras = content if isinstance(content, list) else [content]
    first = True
    for p in paras:
        spec = p if isinstance(p, dict) else {"runs": p}
        runs = spec["runs"]
        if isinstance(runs, str):
            runs = [runs]
        para = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        para.alignment = ALIGN[spec.get("align", align)]
        para.line_spacing = spec.get("spacing", spacing)
        g = spec.get("gap", gap)
        if g:
            para.space_after = Pt(g)
        for seg in runs:
            text_, o = (seg, {}) if isinstance(seg, str) else seg
            r = para.add_run()
            r.text = glue(text_)
            _font(r, o.get("size", spec.get("size", size)), o.get("color", color), o.get("bold", bold),
                  o.get("face", face), o.get("latin", latin))
        if spec.get("bullet"):
            _bullet(para, spec.get("bullet_color", RED))
        elif spec.get("indent"):
            pPr = para._p.get_or_add_pPr()
            pPr.set("marL", str(int(0.2 * E)))
            pPr.set("indent", "0")


def text(s, x, y, w, h, content, size=12, color=TEXT, bold=False, align="l", anchor="t", spacing=1.15,
         gap=0, name=None, wrap=True, face=FONT, latin=None, fill=None):
    tb = s.shapes.add_textbox(I(x), I(y), I(w), I(h))
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = ANCHOR[anchor]
    fill_frame(tf, content, size, color, bold, align, spacing, gap, face, latin)
    if fill:
        tb.fill.solid()
        tb.fill.fore_color.rgb = RGBColor.from_string(fill)
    if name:
        tb.name = name
    return tb


def box(s, x, y, w, h, fill=CARD, line=None, radius=0.06, rect=False, line_w=0.75, name=None, dash=False):
    shp = s.shapes.add_shape(MSO_SHAPE.RECTANGLE if rect else MSO_SHAPE.ROUNDED_RECTANGLE, I(x), I(y), I(w), I(h))
    if not rect:
        shp.adjustments[0] = max(0.0, min(0.5, radius / min(w, h)))
    if fill:
        shp.fill.solid()
        shp.fill.fore_color.rgb = RGBColor.from_string(fill)
    else:
        shp.fill.background()
    if line:
        shp.line.color.rgb = RGBColor.from_string(line)
        shp.line.width = Pt(line_w)
        if dash:
            ln = shp.line._get_or_add_ln()
            ln.append(ln.makeelement(qn("a:prstDash"), {"val": "dash"}))
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    shp.text_frame.text = ""
    shp.name = name or "card"
    return shp


def boxtext(s, x, y, w, h, content, fill=BLUE_SOFT, line=None, size=12, color=TEXT, bold=False, align="c",
            anchor="m", radius=0.06, rect=False, margin=0.06, spacing=1.1, name=None, line_w=0.75, dash=False):
    shp = box(s, x, y, w, h, fill, line, radius, rect, line_w, name or "boxtext", dash)
    tf = shp.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = I(margin)
    tf.margin_top = tf.margin_bottom = I(0.03)
    tf.vertical_anchor = ANCHOR[anchor]
    fill_frame(tf, content, size, color, bold, align, spacing)
    return shp


def arrow(s, x1, y1, x2, y2, color=MUTED, width=1.5, head=True, name="arrow", dash=False):
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, I(x1), I(y1), I(x2), I(y2))
    c.line.color.rgb = RGBColor.from_string(color)
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    if dash:
        ln.append(ln.makeelement(qn("a:prstDash"), {"val": "dash"}))
    if head:
        ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
    c.name = name
    return c


def image(s, path, x, y, w=None, h=None, name="img"):
    iw, ih = PILImage.open(path).size
    if w and not h:
        h = w * ih / iw
    elif h and not w:
        w = h * iw / ih
    pic = s.shapes.add_picture(path, I(x), I(y), I(w), I(h))
    pic.name = name
    return w, h


def tab(s, x, y, label, fill=NAVY, size=13, h=0.36, color=WHITE):
    w = emw(label) * size / 72 + 0.42
    return boxtext(s, x, y, w, h, label, fill=fill, size=size, color=color, bold=True, radius=h / 2, name="tab",
                   margin=0.12)


def card(s, x, y, w, h, title=None, fill=CARD, edge=EDGE, tab_fill=NAVY):
    """浅色卡片，标题做成压在上边沿的深色标签（模板里「子标题」那种）。返回正文起点 y。"""
    box(s, x, y, w, h, fill=fill, line=edge, radius=0.08, name="card")
    if title:
        tab(s, x + 0.2, y - 0.18, title, fill=tab_fill)
        return y + 0.32
    return y + 0.16


def stat(s, x, y, w, value, label, color=NAVY, vsize=26, lsize=11.5, align="l"):
    text(s, x, y, w, 0.52, value, size=vsize, color=color, bold=True, anchor="b", align=align, name="stat")
    text(s, x, y + 0.56, w, 0.32, label, size=lsize, color=MUTED, align=align, name="stat_label")


def footnote(s, content, y=6.94, h=0.26):
    text(s, M, y, W - 2 * M - 1.2, h, content, size=10.5, color=MUTED, name="footnote")


def table(s, x, y, col_w, rows, row_h=0.4, size=11.5, header_size=12, aligns=None, zebra=True, header=True,
          name="table", bold_first_col=False):
    nr, nc = len(rows), len(col_w)
    gf = s.shapes.add_table(nr, nc, I(x), I(y), I(sum(col_w)), I(row_h * nr))
    gf.name = name
    tbl = gf.table
    tblPr = tbl._tbl.tblPr
    tblPr.set("firstRow", "0")
    tblPr.set("bandRow", "0")
    sid = tblPr.find(qn("a:tableStyleId"))
    if sid is None:
        sid = tblPr.makeelement(qn("a:tableStyleId"), {})
        tblPr.append(sid)
    sid.text = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"
    for j, cw in enumerate(col_w):
        tbl.columns[j].width = I(cw)
    for i in range(nr):
        tbl.rows[i].height = I(row_h)
        for j in range(nc):
            val, o = rows[i][j] if isinstance(rows[i][j], tuple) else (rows[i][j], {})
            cell = tbl.cell(i, j)
            hdr = header and i == 0
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            for r_ in list(p.runs):
                r_._r.getparent().remove(r_._r)
            r = p.add_run()
            r.text = glue(str(val))
            _font(r, header_size if hdr else o.get("size", size), WHITE if hdr else o.get("color", TEXT),
                  hdr or o.get("bold", bold_first_col and j == 0))
            a = "c" if hdr else (aligns[j] if aligns else "l")
            p.alignment = ALIGN[o.get("align", a)]
            p.line_spacing = 1.05
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.margin_left = cell.margin_right = I(0.09)
            cell.margin_top = cell.margin_bottom = I(0.03)
            f = NAVY if hdr else o.get("fill", (CARD if (zebra and i % 2 == 0) else WHITE))
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor.from_string(f)
            tcPr = cell._tc.get_or_add_tcPr()
            for k, tag in enumerate(("a:lnL", "a:lnR", "a:lnT", "a:lnB")):
                old = tcPr.find(qn(tag))
                if old is not None:
                    tcPr.remove(old)
                ln = tcPr.makeelement(qn(tag), {"w": "9525", "cap": "flat", "cmpd": "sng", "algn": "ctr"})
                sf = ln.makeelement(qn("a:solidFill"), {})
                sf.append(sf.makeelement(qn("a:srgbClr"), {"val": EDGE}))
                ln.append(sf)
                ln.append(ln.makeelement(qn("a:prstDash"), {"val": "solid"}))
                tcPr.insert(k, ln)
    return gf


def circle_num(s, x, y, d, label, fill=NAVY, size=12):
    shp = s.shapes.add_shape(MSO_SHAPE.OVAL, I(x), I(y), I(d), I(d))
    shp.fill.solid()
    shp.fill.fore_color.rgb = RGBColor.from_string(fill)
    shp.line.fill.background()
    shp.shadow.inherit = False
    tf = shp.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    fill_frame(tf, label, size, WHITE, True, "c", 1.0)
    shp.name = "num"
    return shp


# ------------------------------------------------------------------ 模板页处理
def set_paragraphs(shape, lines, size_pt=None, face=None):
    txBody = shape.text_frame._txBody
    ps = txBody.findall(qn("a:p"))
    while len(ps) < len(lines):
        ps[-1].addnext(copy.deepcopy(ps[-1]))
        ps = txBody.findall(qn("a:p"))
    for p, line in zip(ps, lines):
        runs = p.findall(qn("a:r"))
        for extra in runs[1:]:
            p.remove(extra)
        for tag in ("a:br", "a:fld"):
            for el in p.findall(qn(tag)):
                p.remove(el)
        if not runs:
            r = p.makeelement(qn("a:r"), {})
            r.append(r.makeelement(qn("a:rPr"), {"lang": "zh-CN"}))
            r.append(r.makeelement(qn("a:t"), {}))
            end = p.find(qn("a:endParaRPr"))
            if end is not None:
                end.addprevious(r)
            else:
                p.append(r)
            runs = [r]
        r0 = runs[0]
        r0.find(qn("a:t")).text = line
        rPr = r0.find(qn("a:rPr"))
        if rPr is not None:
            rPr.set("lang", "zh-CN")
            if size_pt:
                rPr.set("sz", str(int(round(size_pt * 100))))
            if face:
                for tag in ("a:latin", "a:ea"):
                    el = rPr.find(qn(tag))
                    if el is not None:
                        el.set("typeface", face)
                        for a in ("panose", "charset", "pitchFamily"):
                            if a in el.attrib:
                                del el.attrib[a]
        end = p.find(qn("a:endParaRPr"))
        if end is not None and size_pt:
            end.set("sz", str(int(round(size_pt * 100))))
    for p in ps[len(lines):]:
        txBody.remove(p)


def strip_tags(slide):
    for el in list(slide._element.iter(qn("p:custDataLst"))):
        el.getparent().remove(el)
    for rId, rel in list(slide.part.rels.items()):
        if rel.reltype.endswith("/tags"):
            slide.part.drop_rel(rId)


def content(dst, label, title):
    keep = {"图片 5", "图片 4", "图片 7", "文本框 1", "矩形: 圆角 18"}
    for sh in list(dst.shapes):
        if sh.is_placeholder or sh.name in keep:
            continue
        sh._element.getparent().remove(sh._element)
    strip_tags(dst)
    lab = next(s for s in dst.shapes if s.name == "文本框 1")
    set_paragraphs(lab, [label], LABEL_PT, face=FONT)
    lab.width = I(2.75)
    pill = next(s for s in dst.shapes if s.name == "矩形: 圆角 18")
    set_paragraphs(pill, [title], PILL_PT)
    w = min(7.30, max(3.60, emw(title) * PILL_PT / 72 + 0.80))
    pill.width = I(w)
    pill.left = I((W - w) / 2)
    return dst


def do_toc(dst):
    rows = sorted([s for s in dst.shapes if s.has_text_frame and abs(s.left / E - 6.76) < 0.1], key=lambda s: s.top)
    assert len(rows) == 5, [s.name for s in rows]
    for s, name in zip(rows, SECTIONS):
        set_paragraphs(s, [name])
    strip_tags(dst)


def do_cover(dst):
    by = {s.name: s for s in dst.shapes}
    set_paragraphs(by["TextBox 2"], ["最终答辩"])
    t = by["文本框 1"]
    set_paragraphs(t, [TITLE], 30)
    t.left, t.width = I(0.30), I(12.73)
    st = by["文本框 2"]
    set_paragraphs(st, [SUBTITLE], 22)
    st.left, st.width = I(1.00), I(11.33)
    rp = by["文本框 4"]
    set_paragraphs(rp, [REPORTER, DATE])
    rp.left, rp.width = I(4.87), I(3.60)
    strip_tags(dst)


def do_end(dst):
    boxes = [s for s in dst.shapes if s.name == "PA-文本框 17"]
    set_paragraphs(min(boxes, key=lambda s: s.top), ["敬请各位老师批评指正"])
    rp = next(s for s in dst.shapes if s.name == "文本框 9")
    set_paragraphs(rp, [REPORTER, DATE])
    rp.left, rp.width = I(10.55), I(2.70)
    rp.top = rp.top - I(0.14)
    strip_tags(dst)


def B(t, **o):
    """带格式的片段"""
    return (t, o)


# ================================================================== 正文各页
L1_, L2_, L3_, L4_, L5_ = "一.研究背景", "二.总体方案", "三.关键技术", "四.测试结果", "五.总结展望"


def s03(s):
    content(s, L1_, "1.1 应用场景与任务要求")
    text(s, M, 1.10, W - 2 * M, 0.62,
         "配电室、管廊、电力站房里设备多，固定摄像头照不全，人工巡检容易漏看，记录也不规范。任务书要求在无人车上做"
         "主动式 AI 巡检：发现异常或拍得不清楚时，车自己停下来补拍，形成可复核的证据。", size=13, spacing=1.2, name="intro")
    tab(s, M, 1.86, "任务书要求的闭环")
    steps = [("发现异常", "巡航中实时检测表计、指示灯和开关"),
             ("判断图像质量", "像素密度、清晰度、拍摄角度是否够读"),
             ("主动补拍", "停车，云台对准，变焦，连拍"),
             ("形成证据", "图像、视频、定位、时间和识别结果"),
             ("上报复核", "上传云端人工复核，断网先存本地")]
    bw, gap = 2.21, (W - 2 * M - 5 * 2.21) / 4
    for i, (t, d) in enumerate(steps):
        x = M + i * (bw + gap)
        boxtext(s, x, 2.34, bw, 0.56, [[B("%d  " % (i + 1), color="A9C4E0", bold=True), B(t, bold=True)]],
                fill=NAVY, color=WHITE, size=14.5, name="flow")
        text(s, x + 0.04, 2.98, bw - 0.08, 0.52, d, size=11.5, color=MUTED, align="c", name="flow_sub")
        if i < 4:
            arrow(s, x + bw + 0.05, 2.62, x + bw + gap - 0.05, 2.62, color=NAVY, width=1.75)
    items = ["无人车、RK3576 主机、相机、云台、定位、通信和车控接口的系统集成",
             "基于 RK3576 的 Linux 边缘计算环境搭建及模型部署",
             "目标检测、已知缺陷识别、未知异常检测和图像质量评价",
             "基于目标跟踪的异常目标持续锁定",
             "主动复核状态机设计，实现停车、居中、变焦、多角度补拍和恢复巡检路线",
             "巡检事件证据包、离线缓存、告警分级和远程上传",
             "云端人工复核、模型版本管理和巡检结果闭环",
             "系统实时性、识别效果、网络中断降级和运行稳定性测试"]
    lw = 7.55
    y0 = card(s, M, 3.92, lw, 3.18, "任务书列出的八项研究内容")
    text(s, M + 0.28, y0 + 0.06, lw - 0.5, 2.7,
         [[B("%d　" % (i + 1), color=NAVY, bold=True), it] for i, it in enumerate(items)],
         size=13, gap=4, spacing=1.1, name="items")
    rx = M + lw + 0.3
    y0 = card(s, rx, 3.92, BODY_R - rx, 3.18, "约束条件")
    text(s, rx + 0.25, y0 + 0.06, BODY_R - rx - 0.45, 2.75, [
        {"runs": [B("首版范围　", bold=True, color=NAVY), "单路 1080p RGB 相机，有限缺陷类别。本组做压力表、指示灯、开关手柄三类"], "gap": 8},
        {"runs": [B("安全　", bold=True, color=NAVY), "AI 只发高层指令：暂停巡检、低速前进、移动至观察点、云台转向/变焦、恢复路线；不直接控制转向、电机扭矩和制动"], "gap": 8},
        {"runs": [B("可靠性　", bold=True, color=NAVY), "网络中断、模型异常、算力不足时安全降级"]},
    ], size=13, spacing=1.15, name="constraints")


def s04(s):
    content(s, L1_, "1.2 研究目标与考核指标")
    text(s, M, 1.10, W - 2 * M, 0.62,
         [[B("研究目标　", bold=True, color=NAVY),
           "巡航时用小模型快速扫描，发现可疑目标就停车、对准、变焦补拍，在复核帧上完成读数和判定，全程留存证据。"]],
         size=13, spacing=1.2, name="goal")
    cols = [1.95, 1.9, 2.25]
    left = [["考核项", "指标", "设定依据"],
            ["读数基本误差", "≤ 0.5 % FS", "工业压力表 0.5 级"],
            ["线性度", "≤ 0.4 % FS", "在基本误差内分配"],
            ["重复性", "≤ 0.4 % FS", "在基本误差内分配"],
            ["复核态像素密度", "≥ 120 px", "由 0.5 % FS 精度反推"],
            ["复核前后像素密度比", "1.8–2.4 倍", "看清目标，又不放大到出框"],
            ["目标检测", "mAP50 ≥ 0.70\n漏检率 ≤ 2 %", "巡航漏检就会漏报"]]
    right = [["考核项", "指标", "设定依据"],
             ["云台超调量", "≤ 10 %", "变焦 3× 时同样满足"],
             ["云台调节时间 / 稳态误差", "≤ 1.5 s / ≤ 20 px", "20 px 死区内视为对准"],
             ["巡航帧率", "≥ 10 fps", "保证跟踪连续"],
             ["端到端复核成功率", "> 85 %", "按设定故障率注入的条件下"],
             ["断网降级", "证据包不丢失", "网络恢复后续传"],
             ["安全边界", "AI 不直接控制\n底层执行机构", "任务书要求"]]
    tab(s, M, 1.9, "测量与识别")
    table(s, M, 2.34, cols, left, row_h=0.5, bold_first_col=True, name="table_left")
    rx = M + sum(cols) + 0.29
    tab(s, rx, 1.9, "控制、运行与安全")
    table(s, rx, 2.34, cols, right, row_h=0.5, bold_first_col=True, name="table_right")
    boxtext(s, M, 6.18, W - 2 * M, 0.56,
            "任务书只规定了能力要求，没有给数值。表中数值由本组设定：精度项参照 0.5 级压力表，其余按系统需要。测试结果见第四部分。",
            fill=BLUE_SOFT, size=12.5, color=NAVY, align="l", margin=0.25, name="band")


def s06(s):
    content(s, L2_, "2.1 主动复核：停车、对准、变焦、补拍")
    _, ih = image(s, os.path.join(DLV, "组长-系统/figures/zoom_compare.png"), M, 1.08, w=W - 2 * M, name="img_zoom")
    half = (W - 2 * M) / 2
    text(s, M, 1.08 + ih + 0.05, half, 0.3, "巡航态 1× 车载画面：压力表像素密度（表盘框宽）50.0 px",
         size=11, color=MUTED, align="c", name="caption")
    text(s, M + half, 1.08 + ih + 0.05, half, 0.3, "停车对准后变焦 3×：150.0 px（虚拟配电室，距离 5 m）",
         size=11, color=MUTED, align="c", name="caption")
    y = 5.0
    stats = [("50.0 px", "巡航 1× 像素密度", NAVY), ("150.0 px", "复核 3× 像素密度", RED),
             ("≤ 0.4 %", "实测与针孔公式的偏差", NAVY), ("120 px", "读数所需最低像素密度", NAVY)]
    for i, (v, l, c) in enumerate(stats):
        stat(s, M + 0.1 + i * 3.14, y, 3.0, v, l, color=c, vsize=28)
    boxtext(s, M, 6.02, W - 2 * M, 0.98,
            "5 m 外的压力表在 1× 下只有 50 px，指针角度的像素采样误差超过 0.5 % FS。所以巡航时只做检测，读数留到复核："
            "停车，云台把目标拉到画面中心，按目标当前像素密度算出变焦倍率，放大到 120 px 以上（瞄准值另留 15 % 余量），再连拍 3 帧，取最清晰的一帧读数。",
            fill=BLUE_SOFT, size=12.5, color=TEXT, align="l", anchor="m", margin=0.25, spacing=1.2, name="band")


def s07(s):
    content(s, L2_, "2.2 系统架构：四个进程与硬件边界")
    top, hh = 1.32, 1.72
    xs = {"cam": (0.42, 1.20), "per": (2.37, 2.20), "mis": (5.32, 2.20), "gw": (8.27, 2.20), "hw": (11.21, 1.70)}

    def proc(key, title, lines, strong=True):
        x, w = xs[key]
        box(s, x, top, w, hh, fill=BLUE_SOFT if strong else CARD, line=NAVY if strong else "9AAFC4",
            radius=0.08, name="proc", dash=not strong, line_w=1.0 if strong else 0.9)
        text(s, x + 0.12, top + 0.14, w - 0.24, 0.36, title, size=14, bold=True, color=NAVY, align="c", name="proc_title")
        text(s, x + 0.12, top + 0.56, w - 0.24, hh - 0.66, lines, size=11.5, color=TEXT, align="c", spacing=1.15,
             name="proc_body")

    proc("cam", "相机", ["1080p", "RGB"], strong=False)
    proc("per", "感知进程", ["L1 检测 · L2 读数", "L2′ OCR · L3 异常", "L4 仲裁 · 10 fps"])
    proc("mis", "任务进程", ["十状态机", "复核预算 · 抑制规则", "云台伺服 PID"])
    proc("gw", "安全网关", ["指令白名单", "五项校验 · 看门狗", "审计日志"])
    proc("hw", "底盘 · 云台", ["执行高层指令", "底盘安全层", "可否决指令"], strong=False)
    ymid = top + hh / 2
    links = [("cam", "per", ["图像帧"]), ("per", "mis", ["IF-1", "检测事件"]), ("mis", "gw", ["IF-2", "高层指令"]),
             ("gw", "hw", ["串口"])]
    for a, b, lab in links:
        x1 = xs[a][0] + xs[a][1]
        x2 = xs[b][0]
        arrow(s, x1 + 0.05, ymid + 0.1, x2 - 0.05, ymid + 0.1, color=NAVY, width=1.75)
        text(s, x1, ymid - 0.46, x2 - x1, 0.5, lab, size=10.5, color=NAVY, bold=True, align="c", anchor="b",
             spacing=1.0, name="link_label")
    # IF-3 回路：网关 → 感知、任务
    ylane = top + hh + 0.3
    gx = xs["gw"][0] + xs["gw"][1] / 2
    px = xs["per"][0] + xs["per"][1] / 2
    mx = xs["mis"][0] + xs["mis"][1] / 2
    arrow(s, gx, top + hh, gx, ylane, color=GREEN, width=1.5, head=False, name="lane")
    arrow(s, gx, ylane, px, ylane, color=GREEN, width=1.5, head=False, name="lane")
    arrow(s, px, ylane, px, top + hh + 0.02, color=GREEN, width=1.5, name="lane")
    arrow(s, mx, ylane, mx, top + hh + 0.02, color=GREEN, width=1.5, name="lane")
    text(s, (px + gx) / 2 - 2.3 + 1.4, ylane + 0.05, 4.6, 0.3, "IF-3 状态报告 20 Hz，安全事件立即插播",
         size=10.5, color=GREEN, bold=True, align="c", name="lane_label")
    # 下排：证据目录 → 上传 → 云端
    by, bh = 4.3, 1.08
    ex, ew = 2.37, 2.20
    ux, uw = 5.32, 2.20
    cx, cw = 8.27, BODY_R - 8.27
    for x, w, t, lines in [(ex, ew, "证据目录", ["复核帧 · 视频片段", "仲裁结果"]),
                           (ux, uw, "上传进程", "打包 · 断点续传 · 断网缓存"),
                           (cx, cw, "云端台账", "浏览器查看证据、人工复核、登记模型版本（已登记 5 条）")]:
        box(s, x, by, w, bh, fill=CARD, line=NAVY, radius=0.08, name="proc")
        text(s, x + 0.12, by + 0.1, w - 0.24, 0.34, t, size=13.5, bold=True, color=NAVY, align="c", name="proc_title")
        text(s, x + 0.12, by + 0.48, w - 0.24, bh - 0.52, lines, size=11.5, align="c", name="proc_body")
    dx = xs["per"][0] + 0.45
    arrow(s, dx, top + hh + 0.02, dx, by - 0.03, color=NAVY, width=1.5)
    text(s, dx + 0.08, ylane + 0.34, 0.9, 0.26, "落盘", size=10.5, color=NAVY, bold=True, name="link_label")
    arrow(s, ex + ew + 0.05, by + bh / 2, ux - 0.05, by + bh / 2, color=NAVY, width=1.75)
    arrow(s, ux + uw + 0.05, by + bh / 2, cx - 0.05, by + bh / 2, color=NAVY, width=1.75)
    text(s, ux + uw, by + bh / 2 - 0.34, cx - ux - uw, 0.3, "IF-4", size=10.5, color=NAVY, bold=True, align="c",
         name="link_label")
    y0 = card(s, M, 5.86, 6.1, 1.24, "进程通信与接口")
    text(s, M + 0.25, y0 + 0.05, 5.65, 0.85,
         "四个进程之间用 ZeroMQ 通信。四条接口的报文格式由五份 JSON Schema 约束（ICD v2.1），59 项一致性校验检查代码、Schema 和文档三者对得上。",
         size=12, spacing=1.15, name="card_body")
    rx = M + 6.1 + 0.29
    y0 = card(s, rx, 5.86, BODY_R - rx, 1.24, "AI 能发的指令")
    text(s, rx + 0.25, y0 + 0.05, BODY_R - rx - 0.45, 0.85,
         "暂停巡检、低速前进、移动至巡检位、云台转向/变焦、恢复路线。转向、电机扭矩和制动不在指令集里，网关收到直接拒绝。",
         size=12, spacing=1.15, name="card_body")


def s08(s):
    content(s, L2_, "2.3 复核流程：十状态机、复核预算与抑制规则")
    states = [("CRUISE", "巡航", GREEN_SOFT), ("SUSPECT", "可疑判定", AMBER_SOFT), ("HALT_REQ", "请求停车", BLUE_SOFT),
              ("AIM", "云台对准", BLUE_SOFT), ("ZOOM", "变焦", BLUE_SOFT), ("CAPTURE", "连拍 3 帧", BLUE_SOFT),
              ("VERIFY", "复核推理", BLUE_SOFT), ("PACK", "打包证据", BLUE_SOFT), ("RESUME", "恢复路线", GREEN_SOFT)]
    bw = 1.2
    gap = (W - 2 * M - 9 * bw) / 8
    y, bh = 1.3, 0.86
    cx = []
    for i, (en, zh, f) in enumerate(states):
        x = M + i * (bw + gap)
        cx.append(x + bw / 2)
        boxtext(s, x, y, bw, bh, [{"runs": [B(en, bold=True, color=NAVY, size=10.5, latin=MONO)]},
                                  {"runs": [B(zh, size=11.5)]}], fill=f, line=NAVY, line_w=0.9, spacing=1.05,
                name="state", margin=0.03)
        if i < 8:
            arrow(s, x + bw + 0.02, y + bh / 2, x + bw + gap - 0.02, y + bh / 2, color=NAVY, width=1.25)
    # 中止支路
    yb = y + bh + 0.2
    for i in range(2, 7):
        arrow(s, cx[i], y + bh, cx[i], yb, color=RED, width=1.0, head=False, name="bracket")
    arrow(s, cx[2], yb, cx[6], yb, color=RED, width=1.0, head=False, name="bracket")
    ya = yb + 0.28
    arrow(s, cx[4], yb, cx[4], ya - 0.02, color=RED, width=1.25)
    text(s, cx[4] + 0.12, yb + 0.02, 2.4, 0.26, "超时、对焦失败或安全事件", size=10.5, color=RED, bold=True, name="abort_label")
    aw = 4.3
    boxtext(s, cx[4] - aw / 2, ya, aw, 0.5,
            [[B("ABORT ", bold=True, color=RED, latin=MONO), B("中止复核：恢复路线，照常打包并记下中止原因", size=11.5)]],
            fill=RED_SOFT, line=RED, line_w=0.9, name="abort", size=11.5)
    # 回到巡航
    yl = ya + 0.5 + 0.22
    arrow(s, cx[8], y + bh, cx[8], yl, color=GREEN, width=1.5, head=False, name="lane")
    arrow(s, cx[8], yl, cx[0], yl, color=GREEN, width=1.5, head=False, name="lane")
    arrow(s, cx[0], yl, cx[0], y + bh + 0.02, color=GREEN, width=1.5, name="lane")
    arrow(s, cx[4], ya + 0.5, cx[4], yl - 0.01, color=RED, width=1.0, head=False, name="lane")
    text(s, cx[0] + 0.15, yl + 0.04, 2.0, 0.26, "回到巡航", size=10.5, color=GREEN, bold=True, name="lane_label")
    cy, ch = 4.05, 3.05
    cw = (W - 2 * M - 0.6) / 3
    x0 = M
    y0 = card(s, x0, cy, cw, ch, "进入复核的条件")
    text(s, x0 + 0.25, y0 + 0.08, cw - 0.45, 1.75, [
        {"runs": ["检出置信度在 0.25–0.60 之间"], "bullet": True, "gap": 5},
        {"runs": ["目标像素密度低于 120 px"], "bullet": True, "gap": 5},
        {"runs": ["读数落在正常范围之外"], "bullet": True, "gap": 5},
        {"runs": ["图像质量分低于阈值"], "bullet": True, "gap": 5},
        {"runs": ["L3 报未知异常"], "bullet": True}], size=12.5, name="card_body")
    text(s, x0 + 0.25, y0 + 2.02, cw - 0.45, 0.46, "置信度 ≥ 0.60、像素密度够、读数正常的检出不进复核。",
         size=11.5, color=MUTED, name="card_note")
    x1 = M + cw + 0.3
    y0 = card(s, x1, cy, cw, ch, "复核预算")
    text(s, x1 + 0.25, y0 + 0.1, cw - 0.45, 0.46, "N_max = ⌊(T_max − L/v) / T_r⌋", size=16, bold=True, color=NAVY,
         align="c", latin="Cambria Math", name="formula")
    text(s, x1 + 0.25, y0 + 0.72, cw - 0.45, 1.9,
         "一轮巡检能停车复核的次数有上限。按当前配置 60 m 路线、0.25 m/s、600 s 时限、单次复核 T_r = 9.2 s，一轮最多复核 39 次；"
         "T_r 由各状态的时间预算实时加总。", size=12, spacing=1.2, name="card_body")
    x2 = M + 2 * (cw + 0.3)
    y0 = card(s, x2, cy, cw, ch, "三条抑制规则")
    text(s, x2 + 0.25, y0 + 0.08, cw - 0.45, 2.5, [
        {"runs": [B("同目标冷却　", bold=True, color=NAVY), "同一目标 60 s 内不重复复核"], "bullet": True, "gap": 7},
        {"runs": [B("同巡检位单次　", bold=True, color=NAVY), "2 m 内本轮只复核一次，防止跟踪丢 ID 后重复复核"], "bullet": True, "gap": 7},
        {"runs": [B("恢复静默　", bold=True, color=NAVY), "恢复巡航后 3 s 内不进入复核，避免起步时连续误触发"], "bullet": True}],
         size=12, spacing=1.15, name="card_body")


def s09(s):
    content(s, L2_, "2.4 证据包、离线缓存与云端复核")
    tab(s, M, 1.08, "一次复核的实例（虚拟配电室）")
    iw = 3.08
    _, ih = image(s, os.path.join(IMG, "ev_cruise.jpg"), M, 1.52, w=iw, name="img_cruise")
    image(s, os.path.join(IMG, "ev_verify.jpg"), M + iw + 0.22, 1.52, w=iw, name="img_verify")
    text(s, M, 1.52 + ih + 0.05, iw, 0.3, "巡航帧：压力表 49 px，置信度 0.91", size=11, color=MUTED, align="c", name="caption")
    text(s, M + iw + 0.22, 1.52 + ih + 0.05, iw, 0.3, "复核帧：变焦 2.81×，139 px", size=11, color=MUTED, align="c",
         name="caption")
    ry = 3.72
    lw = 2 * iw + 0.22
    box(s, M, ry, lw, 1.52, fill=GREEN_SOFT, line="A8D5BA", radius=0.08, name="card")
    image(s, os.path.join(IMG, "ev_roi.png"), M + lw - 1.37, ry + 0.13, w=1.25, name="img_roi")
    text(s, M + 0.22, ry + 0.12, lw - 1.75, 1.3, [
        {"runs": [B("结论　读数正常 READING_OK", bold=True, color=GREEN, size=13)], "gap": 4},
        {"runs": ["读数 0.955 MPa，量程 0–1.6 MPa，在正常范围内"], "gap": 2},
        {"runs": ["OCR 读到刻度 0.4、0.8，落在标定量程内，互证一致"], "gap": 2},
        {"runs": ["像素密度 49 → 139 px，复核前后比 2.84"]}], size=11.5, spacing=1.1, name="card_body")
    rx = M + lw + 0.35
    rw = BODY_R - rx
    y0 = card(s, rx, 1.26, rw, 3.98, "证据包里有什么")
    files = [("cruise.jpg / cruise_raw.jpg", "巡航帧，带框与原图"), ("cruise_clip.mp4", "触发前 3 s 的视频回放"),
             ("verify_01–03.jpg", "复核连拍 3 帧"), ("verify_roi.jpg", "目标区域"), ("fusion.json", "结论与逐条理由"),
             ("manifest.json", "巡检位、时间、结论、文件清单"), ("meta.jsonl", "复核期间的状态流水")]
    for i, (f, d) in enumerate(files):
        yy = y0 + 0.08 + i * 0.49
        text(s, rx + 0.25, yy, 2.55, 0.42, f, size=11.5, color=NAVY, bold=True, face=FONT, latin=MONO, anchor="m",
             name="file")
        text(s, rx + 2.85, yy, rw - 3.05, 0.42, d, size=11.5, anchor="m", name="file_desc")
    fy = 5.52
    flow = [("落盘", "证据目录"), ("上传队列", "断点续传、指数退避"), ("云端台账", "FastAPI + SQLite"),
            ("人工复核", "浏览器查看证据与结论")]
    fw = 2.6
    fg = (W - 2 * M - 4 * fw) / 3
    for i, (t, d) in enumerate(flow):
        x = M + i * (fw + fg)
        boxtext(s, x, fy, fw, 0.62, [{"runs": [B(t, bold=True, color=NAVY, size=13)]}, {"runs": [B(d, size=11, color=MUTED)]}],
                fill=BLUE_SOFT, line=NAVY, line_w=0.9, spacing=1.0, name="flow")
        if i < 3:
            arrow(s, x + fw + 0.05, fy + 0.31, x + fw + fg - 0.05, fy + 0.31, color=NAVY, width=1.75)
    text(s, M, 6.3, W - 2 * M, 0.72, [
        {"runs": [B("断网　", bold=True, color=NAVY), "证据包留在本地，上传进程按退避策略一直重试，网络恢复后从断点接着传。"], "bullet": True, "gap": 3},
        {"runs": [B("告警分级　", bold=True, color=NAVY), "可疑目标一确认，先给云端发一条轻量告警；复核结论按 INFO / WARN / CRITICAL 分级。"], "bullet": True}],
         size=12, name="notes")


def s10(s):
    content(s, L2_, "2.5 虚拟配电室：验证与展示平台")
    _, ih = image(s, os.path.join(DLV, "组长-系统/figures/thirdperson_compare.png"), M, 1.08, w=W - 2 * M,
                  name="img_third")
    text(s, M, 1.08 + ih + 0.05, W - 2 * M, 0.3,
         "第三人称视角：巡航态视锥 60°（左）；复核时变焦 3×，视锥收窄到 21.8° 并套住目标表计（右）",
         size=11, color=MUTED, name="caption")
    cy, ch = 5.22, 1.88
    cw = (W - 2 * M - 0.6) / 3
    cards = [("成像模型",
              "针孔相机模型，实测水平视场角 59.95°（标称 60°）。像素密度、读数精度、云台阶跃响应都在场景里实测；读数算法不读场景真值。"),
             ("故障注入",
              "按设定故障率注入：停车延迟 1.5–2.5 s，指令丢包 2 %，对焦失败 5 %，安全事件每分钟 0.05 次。在这些故障下，复核流程仍能走完并产出证据包。"),
             ("驱动接口",
              "驱动层有底盘、云台、相机、定位四个抽象接口，driver_mode 由 stub 改为 real 即换成串口与 V4L2 驱动。串口协议已用假小车逐字节走通。")]
    for i, (t, b) in enumerate(cards):
        x = M + i * (cw + 0.3)
        y0 = card(s, x, cy, cw, ch, t)
        text(s, x + 0.25, y0 + 0.06, cw - 0.45, ch - 0.45, b, size=12, spacing=1.2, name="card_body")


def s12(s):
    content(s, L3_, "3.1 识别层：四路模型与规则仲裁")
    boxtext(s, M, 2.12, 1.25, 0.9, [{"runs": [B("复核帧", bold=True, size=13)]}], fill=CARD, line="9AAFC4", name="flow",
            dash=True)
    boxtext(s, 2.02, 2.02, 2.1, 1.1, [{"runs": [B("L1 目标检测", bold=True, size=14)]}, {"runs": [B("YOLO11 两级", size=12)]}],
            fill=NAVY, color=WHITE, name="flow")
    arrow(s, M + 1.25 + 0.05, 2.57, 2.02 - 0.05, 2.57, color=NAVY, width=1.75)
    mids = [("L2 读数", "几何解算指针角度"), ("L2′ OCR 互证", "读刻度数字、单位、位置牌"), ("L3 未知异常", "PaDiM，只用正常样本")]
    ys = [1.22, 2.17, 3.12]
    for (t, d), yy in zip(mids, ys):
        boxtext(s, 4.62, yy, 3.1, 0.8, [{"runs": [B(t, bold=True, size=13.5, color=NAVY)]}, {"runs": [B(d, size=11.5)]}],
                fill=BLUE_SOFT, line=NAVY, line_w=0.9, name="flow")
        arrow(s, 4.12 + 0.05, 2.57, 4.62 - 0.05, yy + 0.4, color=NAVY, width=1.25)
        arrow(s, 7.72 + 0.05, yy + 0.4, 8.22 - 0.05, 2.57, color=NAVY, width=1.25)
    boxtext(s, 8.22, 2.02, 1.75, 1.1, [{"runs": [B("L4 仲裁", bold=True, size=14)]}, {"runs": [B("纯规则", size=12)]}],
            fill=RED, color=WHITE, name="flow")
    arrow(s, 9.97 + 0.05, 2.57, 10.42 - 0.05, 2.57, color=NAVY, width=1.75)
    verdicts = [("确认缺陷", "CONFIRMED_DEFECT", RED_SOFT), ("误报消解", "FALSE_ALARM", CARD),
                ("读数正常", "READING_OK", GREEN_SOFT), ("读数异常", "READING_ABNORMAL", AMBER_SOFT),
                ("未知异常", "UNKNOWN_ANOMALY", BLUE_SOFT), ("证据不足", "INCONCLUSIVE", CARD)]
    for i, (zh, en, f) in enumerate(verdicts):
        boxtext(s, 10.42, 1.22 + i * 0.47, BODY_R - 10.42, 0.4,
                [[B(zh, bold=True, size=12), B("  " + en, size=9.5, color=MUTED, latin=MONO)]], fill=f, line=EDGE,
                align="l", margin=0.12, name="chip")
    cy, ch = 4.35, 2.4
    lw = 7.9
    y0 = card(s, M, cy, lw, ch, "仲裁顺序")
    rules = [("OCR 读出另一套量程，或单位不是同一物理量", "判证据不足，交人工核对"),
             ("像素密度低于 96 px（判据线的 80 %）", "不下读数类结论"),
             ("有读数时以读数为准", "L3 报的异常转人工复核，不直接告警"),
             ("其余证据不足的情况", "判证据不足，交人工")]
    for i, (a, b) in enumerate(rules):
        yy = y0 + 0.1 + i * 0.47
        circle_num(s, M + 0.25, yy + 0.04, 0.32, str(i + 1), size=11.5)
        text(s, M + 0.72, yy, lw - 1.0, 0.42, [[B(a, bold=True, color=NAVY), "：" + b]], size=12.5, anchor="m",
             name="rule")
    rx = M + lw + 0.3
    y0 = card(s, rx, cy, BODY_R - rx, ch, "结论附逐条理由")
    text(s, rx + 0.25, y0 + 0.08, BODY_R - rx - 0.45, 1.95, [
        {"runs": ["上一页那次复核写进 fusion.json 的理由："], "gap": 5, "size": 11.5, "spacing": 1.1},
        {"runs": [B("OCR 互证一致：读到刻度 [0.4, 0.8]，先验量程 [0, 1.6]，全部落在量程内", size=11, color=NAVY)], "gap": 4, "bullet": True, "bullet_color": NAVY},
        {"runs": [B("读数 0.955 落在正常带内 → READING_OK", size=11, color=NAVY)], "bullet": True, "bullet_color": NAVY}],
         size=11.5, spacing=1.12, name="card_body")
    footnote(s, "六种结论各有测试用例；仲裁不含可学习参数，同样的四路输入总得到同样的结论。", y=6.94)


def s13(s):
    content(s, L3_, "3.2 L1 目标检测：YOLO11 两级检测")
    lw = 6.2
    y0 = card(s, M, 1.3, lw, 2.28, "模型配置")
    text(s, M + 0.25, y0 + 0.08, lw - 0.45, 1.85, [
        {"runs": [B("巡航级 yolo11s　", bold=True, color=NAVY), "置信度阈值 0.25，每帧运行"], "bullet": True, "gap": 6},
        {"runs": [B("复核级 yolo11m　", bold=True, color=NAVY), "置信度阈值 0.60，只在复核帧上运行"], "bullet": True, "gap": 6},
        {"runs": [B("输入 1280 × 1280　", bold=True, color=NAVY), "1× 巡航时目标只有 40–60 px"], "bullet": True, "gap": 6},
        {"runs": [B("检测三类　", bold=True, color=NAVY), "压力表、指示灯、开关手柄"], "bullet": True}],
         size=13, spacing=1.15, name="card_body")
    y0 = card(s, M, 3.9, lw, 1.72, "训练数据")
    text(s, M + 0.25, y0 + 0.08, lw - 0.45, 1.3, [
        {"runs": ["公开数据集训练基线模型"], "bullet": True, "gap": 4},
        {"runs": ["混入虚拟配电室合成渲染图（×3）微调 15 轮"], "bullet": True, "gap": 4},
        {"runs": ["按图像基名去重，训练集与验证集之间无同源图像"], "bullet": True}],
         size=13, spacing=1.12, name="card_body")
    rx = M + lw + 0.35
    rw = BODY_R - rx
    ih = 4.26
    iw = ih * 1280 / 888
    image(s, os.path.join(IMG, "l1_val_pred.jpg"), rx + (rw - iw) / 2, 1.1, h=ih, name="img_val")
    text(s, rx, 1.1 + ih + 0.04, rw, 0.28, "巡航级模型在真实验证集上的检测样例", size=11, color=MUTED, align="c",
         name="caption")
    box(s, M, 5.9, W - 2 * M, 0.98, fill=BLUE_SOFT, radius=0.08, name="card")
    stats = [("0.9941", "mAP50（两级相同）", RED), ("0.168 %", "巡航级漏检率", NAVY), ("0.185 %", "复核级漏检率", NAVY),
             ("22 / 43 ms", "单帧耗时，巡航级 / 复核级", NAVY)]
    for i, (v, l, c) in enumerate(stats):
        stat(s, M + 0.35 + i * 3.1, 5.86, 2.9, v, l, color=c, vsize=24, lsize=11.5)
    footnote(s, "两级指标均为微调后模型（cruise_ft / verify_ft）在同一无泄漏验证集上的结果；耗时在 RTX 3060 上以 1280 输入测得。", y=6.96)


def s14(s):
    content(s, L3_, "3.3 L2 指针读数：几何解算与 OCR 互证")
    tab(s, M, 1.08, "读数处理链")
    steps = [("裁剪 ROI", "取复核级检测框内的图像"), ("定位表盘", "椭圆拟合"),
             ("透视校正", ["椭圆拉回正圆；", "短长轴比 < 0.85 时读数置信度打折"]),
             ("提取指针", ["极坐标展开，在 0.28R–0.70R 径向带内", "找暗像素覆盖率峰值"]), ("指针方向", "逐环计算角质心取中位数；径向带避开配重尾巴"),
             ("换算读数", "代入五点标定曲线")]
    for i, (t, d) in enumerate(steps):
        yy = 1.56 + i * 0.66
        circle_num(s, M, yy + 0.07, 0.36, str(i + 1), size=12.5)
        text(s, M + 0.5, yy, 1.3, 0.5, t, size=13, bold=True, color=NAVY, anchor="m", name="step_title")
        text(s, M + 1.82, yy, 3.75, 0.5, d, size=12, anchor="m", spacing=1.1, name="step_desc")
    box(s, M, 5.72, 5.55, 1.08, fill=BLUE_SOFT, radius=0.08, name="card")
    for i, (v, l, c) in enumerate([("0.455 % FS", "基本误差", RED), ("0.302 % FS", "线性度", NAVY), ("0.309 % FS", "重复性", NAVY)]):
        stat(s, M + 0.24 + i * 1.8, 5.78, 1.72, v, l, color=c, vsize=17, lsize=11.5)
    rx = 6.25
    image(s, os.path.join(IMG, "ev_roi.png"), rx + 0.08, 1.12, w=2.3, name="img_roi")
    text(s, rx, 3.46, 2.46, 0.28, "复核 ROI，139 px", size=11, color=MUTED, align="c", name="caption")
    cxr = 8.9
    _, ch = image(s, os.path.join(IMG, "calib_curve_final.png"), cxr, 1.06, w=BODY_R - cxr, name="img_calib")
    text(s, cxr, 1.06 + ch + 0.02, BODY_R - cxr, 0.28, "一次五点标定，每点读 10 次", size=11, color=MUTED, align="c",
         name="caption")
    y0 = card(s, rx, 4.46, BODY_R - rx, 2.34, "OCR 互证（RapidOCR，离线权重）")
    text(s, rx + 0.25, y0 + 0.1, BODY_R - rx - 0.45, 1.85, [
        {"runs": ["读出表盘上的刻度数字和单位，与标定表比对"], "bullet": True, "gap": 6},
        {"runs": ["读出另一套量程，或单位不是同一物理量：判证据不足"], "bullet": True, "gap": 6},
        {"runs": ["开关类目标读位置牌文字，与几何法结果互证"], "bullet": True, "gap": 6},
        {"runs": ["刻度数字 90 px、单位 120 px 起可读（正对表盘）"], "bullet": True, "gap": 6},
        {"runs": ["各档位下与正确量程冲突的误判 0 次"], "bullet": True}],
         size=13, spacing=1.15, name="card_body")
    footnote(s, "读数三项为 43 次独立标定的中位，每次五点各测 10 次。读数算法不读指针真值；标定时目标框由渲染器给出。", y=6.96)


def s15(s):
    content(s, L3_, "3.4 L3 未知异常检测与 RKNN 部署")
    tab(s, M, 1.08, "PaDiM：只用正常样本学习“正常长什么样”")
    steps = [("复核 ROI", ""), ("缩放到 256×256", ""), ("ResNet18 特征", "取 layer2、layer3"),
             ("逐位置高斯分布", "全协方差，正常样本估计"), ("马氏距离", "得到异常分")]
    bw = 2.18
    gap = (W - 2 * M - 5 * bw) / 4
    for i, (t, d) in enumerate(steps):
        x = M + i * (bw + gap)
        paras = [{"runs": [B(t, bold=True, size=13, color=NAVY)]}]
        if d:
            paras.append({"runs": [B(d, size=11)]})
        boxtext(s, x, 1.56, bw, 0.9, paras, fill=BLUE_SOFT, line=NAVY, line_w=0.9, spacing=1.05, name="flow")
        if i < 4:
            arrow(s, x + bw + 0.04, 2.01, x + bw + gap - 0.04, 2.01, color=NAVY, width=1.75)
    stats = [("3.3 %", "漏报率", RED), ("3.8 %", "误报率", NAVY), ("0.55", "判定阈值，约 3.3σ", NAVY), ("51.6 ms", "单次打分（PC 端 CPU）", NAVY)]
    for i, (v, l, c) in enumerate(stats):
        stat(s, M + 0.1 + i * 3.14, 2.66, 3.0, v, l, color=c, vsize=28)
    cy, ch = 4.12, 2.66
    cw = (W - 2 * M - 0.6) / 3
    blocks = [("训练与评测", ["训练集 796 张正常裁片，全部由虚拟配电室生成，可以重建",
                            "评测集 106 张正常、120 张异常",
                            "异常分：偏离正常分布的 σ 数除以 6"]),
              ("使用约束", ["只进人工复核队列，不直接告警",
                          "像素密度 ≥ 60 px 才打分",
                          "巡航态每帧只给密度最高的 1 个目标打分，复核态不限"]),
              ("RKNN 部署（RK3576）", ["两个特征子网转为 INT8 模型，用 100 张正常样本做量化校准",
                                   "特征相对误差 5.40 % / 4.96 %，余弦相似度 0.9986 / 0.9989",
                                   "整套评测集上误报、漏报与 PC 端相同，没有一条判定改变"])]
    for i, (t, items) in enumerate(blocks):
        x = M + i * (cw + 0.3)
        y0 = card(s, x, cy, cw, ch, t)
        text(s, x + 0.25, y0 + 0.08, cw - 0.45, ch - 0.5,
             [{"runs": [it], "bullet": True, "gap": 8} for it in items], size=13, spacing=1.15, name="card_body")


def s16(s):
    content(s, L3_, "3.5 三层安全防线")
    layers = [("协议层", "限制 AI 能表达什么",
               "接口只定义了 7 条高层指令，报文格式由 Schema 约束。转向、电机扭矩、制动没有对应字段，AI 无从表达。"),
              ("网关层", "限制指令能否被接受",
               "五项校验：白名单、Schema、参数范围、状态冲突、安全覆盖，范围值写在源码里。不合格的指令被拒绝，校验结果逐项写入审计日志。1500 ms 收不到心跳，网关自行让车恢复路线。"),
              ("优先级层", "限制指令是否执行",
               "底盘安全层独立运行，可以否决任何上层指令；上位机保活超时，底盘自行减速停车。")]
    lw = 7.4
    y = 1.22
    hs = [1.38, 1.9, 1.38]
    for (t, sub, body), h_ in zip(layers, hs):
        box(s, M, y, lw, h_, fill=CARD, line=EDGE, radius=0.08, name="card")
        boxtext(s, M, y, 1.7, h_, [{"runs": [B(t, bold=True, size=17)]}, {"runs": [B(sub, size=11)]}], fill=NAVY,
                color=WHITE, radius=0.08, spacing=1.15, name="layer")
        text(s, M + 1.92, y + 0.12, lw - 2.12, h_ - 0.24, body, size=12.5, anchor="m", spacing=1.2, name="layer_body")
        y += h_ + 0.16
    rx = M + lw + 0.3
    rw = BODY_R - rx
    y0 = card(s, rx, 1.4, rw, 4.66, "现场演示")
    demos = [("越界指令被拒，审计日志记下哪一项没过", "pytest tests/test_gateway.py -k out_of_range"),
             ("杀掉任务进程，看门狗让车自己走完路线", "pkill -f patrol.mission.node"),
             ("注入安全事件，进行中的复核 200 ms 内中止", "pytest tests/test_fsm.py -k safety")]
    for i, (d, cmd) in enumerate(demos):
        yy = y0 + 0.16 + i * 1.4
        circle_num(s, rx + 0.22, yy + 0.02, 0.34, str(i + 1), size=12)
        text(s, rx + 0.68, yy, rw - 0.9, 0.4, d, size=12.5, anchor="m", name="demo")
        cb = boxtext(s, rx + 0.22, yy + 0.52, rw - 0.44, 0.44, cmd, fill=WHITE, line=EDGE, size=10.5, color=NAVY,
                     align="l", margin=0.12, name="cmd")
        for r_ in cb.text_frame.paragraphs[0].runs:
            _font(r_, 10.5, NAVY, False, face=FONT, latin=MONO)
    boxtext(s, M, 6.40, W - 2 * M, 0.52, "底盘安全层不经过 AI 和网关：即使网关失效，底盘仍能自行制动。",
            fill=BLUE_SOFT, size=12.5, color=NAVY, bold=True, align="l", margin=0.25, name="band")


def s17(s):
    content(s, L3_, "3.6 云台伺服与变焦增益调度")
    tab(s, M, 1.08, "控制回路")
    yc = 2.08
    text(s, M, yc - 0.36, 0.8, 0.3, "W/2", size=12, bold=True, color=NAVY, align="c", latin=MONO, name="loop_label")
    arrow(s, M + 0.1, yc, 1.3, yc, color=NAVY, width=1.5)
    sx = 1.34
    circ = s.shapes.add_shape(MSO_SHAPE.OVAL, I(sx), I(yc - 0.2), I(0.4), I(0.4))
    circ.fill.solid()
    circ.fill.fore_color.rgb = RGBColor.from_string(WHITE)
    circ.line.color.rgb = RGBColor.from_string(NAVY)
    circ.line.width = Pt(1.25)
    circ.shadow.inherit = False
    circ.name = "sum"
    tfc = circ.text_frame
    tfc.margin_left = tfc.margin_right = tfc.margin_top = tfc.margin_bottom = 0
    tfc.vertical_anchor = MSO_ANCHOR.MIDDLE
    fill_frame(tfc, "Σ", 13, NAVY, True, "c", 1.0)
    blocks = [(1.98, 1.3, ["PID", "Kp 3.0 · Ki 1.2", "Kd 0.12"]), (3.54, 1.3, ["× θ/(W·z)", "增益调度"]),
              (5.1, 0.86, ["云台", "角速度 ω"])]
    prev = sx + 0.4
    for x, w, lines in blocks:
        arrow(s, prev + 0.02, yc, x - 0.03, yc, color=NAVY, width=1.5)
        paras = [{"runs": [B(lines[0], bold=True, size=13, color=NAVY)]}] + [{"runs": [B(t, size=10, color=MUTED)]} for t in lines[1:]]
        boxtext(s, x, yc - 0.42, w, 0.84, paras, fill=BLUE_SOFT, line=NAVY, line_w=0.9, spacing=1.0, name="flow",
                margin=0.03)
        prev = x + w
    yf = yc + 0.72
    lx = prev + 0.1
    arrow(s, prev, yc, lx, yc, color=NAVY, width=1.5, head=False, name="lane")
    arrow(s, lx, yc, lx, yf, color=NAVY, width=1.5, head=False, name="lane")
    arrow(s, lx, yf, sx + 0.2, yf, color=NAVY, width=1.5, head=False, name="lane")
    arrow(s, sx + 0.2, yf, sx + 0.2, yc + 0.22, color=NAVY, width=1.5, name="lane")
    text(s, 1.9, yf + 0.05, 4.1, 0.28, "反馈：目标检测框形心在画面中的位置 x", size=11, color=MUTED, align="c",
         name="loop_label")
    lw = 5.62
    y0 = card(s, M, 3.56, lw, 1.52, "增益调度")
    text(s, M + 0.25, y0 + 0.06, lw - 0.45, 1.08,
         "变焦 z 倍后，同样的像素偏差只对应 1/z 的角度偏差。PID 输出乘以 θ/(W·z) 再下发，各变焦倍率下环路增益保持一致。偏差超过 150 px 时暂停积分（积分分离）。",
         size=12.5, spacing=1.2, name="card_body")
    y0 = card(s, M, 5.48, lw, 1.36, "先对准，再变焦")
    text(s, M + 0.25, y0 + 0.06, lw - 0.45, 0.92,
         "AIM 在广角端把目标转到画面中心，ZOOM 再放大。变焦后视场只剩 21.8°，这样转向时目标不会划出画面。",
         size=12.5, spacing=1.2, name="card_body")
    rx = 6.34
    rw = BODY_R - rx
    _, ih = image(s, os.path.join(IMG, "pid_step_final.png"), rx, 1.1, w=rw, name="img_pid")
    text(s, rx, 1.1 + ih + 0.02, rw, 0.28, "云台阶跃响应（虚拟配电室）", size=11, color=MUTED, align="c", name="caption")
    rows = [["变焦倍率", "超调量", "调节时间", "稳态误差"],
            ["1×", "4.1 %", "0.80 s", "10.4 px"],
            ["3×", ("1.0 %", {"bold": True, "color": RED}), ("1.10 s", {"bold": True, "color": RED}), ("8.4 px", {"bold": True, "color": RED})],
            [("限值", {"color": MUTED}), ("≤ 10 %", {"color": MUTED}), ("≤ 1.5 s", {"color": MUTED}), ("≤ 20 px", {"color": MUTED})]]
    table(s, rx, 4.52, [rw / 4] * 4, rows, row_h=0.5, size=13, header_size=12.5, aligns=["c"] * 4, bold_first_col=True,
          name="table_pid")
    footnote(s, "PID 死区 20 px；复现：python -m patrol.tools.tune_pid", y=6.96)


def s19(s):
    content(s, L4_, "4.1 指标达成情况")
    ok = ("达标", {"bold": True, "color": GREEN, "align": "c"})
    part = ("部分达成", {"bold": True, "color": AMBER, "align": "c"})
    rows = [["考核项", "指标", "虚拟配电室实测", "结果"],
            ["读数基本误差", "≤ 0.5 % FS", "0.455 % FS", ok],
            ["线性度", "≤ 0.4 % FS", "0.302 % FS", ok],
            ["重复性", "≤ 0.4 % FS", "0.309 % FS", ok],
            ["复核态像素密度", "≥ 120 px", "149.5 px（标定工况）", ok],
            ["复核前后像素密度比", "1.8–2.4 倍", "1.94–2.32 倍", ok],
            ["目标检测", "mAP50 ≥ 0.70，漏检率 ≤ 2 %", "mAP50 0.9941，漏检率 0.168 % / 0.185 %", ok],
            ["未知异常检测", "不依赖缺陷标注", "漏报 3.3 %，误报 3.8 %", ok],
            ["云台阶跃响应（3×）", "超调 ≤ 10 %，调节 ≤ 1.5 s，稳态 ≤ 20 px", "1.0 %，1.10 s，8.4 px", ok],
            ["连续运行", "30 分钟四进程不退出", "30 分钟无退出、无 CRITICAL，证据包 7 个、复核 7/7", ok],
            ["巡航帧率", "≥ 10 fps", "合成检测器 10 fps；真权重 8.3 fps（渲染占 61.8 ms）", part],
            ["断网降级", "证据包不丢失", "断网 75 s：4 包全部留在本地待续传", ok],
            ["安全边界", "AI 不直接控制底层执行机构", "三层防线，三条演示可当场执行", ok],
            ["端到端复核成功率", "> 85 %", "真权重两轮 7/7（含 30 分钟长稳）；三轮 180 s 中位 83.3 %", part]]
    table(s, M, 1.10, [2.5, 3.72, 4.75, 1.52], rows, row_h=0.385, size=12, bold_first_col=True, name="table_main")
    footnote(s, "读数三项为 43 次独立标定中位；检测为无泄漏真实验证集结果；未知异常为离线评测集（106 正常 + 120 异常）结果；云台为阶跃测试结果；其余为虚拟配电室端到端运行，按设定故障率注入。",
             y=6.74, h=0.42)


def s20(s):
    content(s, L4_, "4.2 端到端运行与断网降级")
    cy, ch = 1.32, 3.62
    cw = (W - 2 * M - 0.6) / 3
    x = M
    y0 = card(s, x, cy, cw, ch, "连续运行")
    rows = [["轮次", "时长", "证据包", "退出"], ["长稳", "30 min", "7", "无"],
            ["1", "300 s", "8", "无"], ["2", "300 s", "7", "无"], ["3", "300 s", "7", "无"]]
    table(s, x + 0.2, y0 + 0.08, [0.78, 0.92, 0.95, 0.92], rows, row_h=0.4, size=12, aligns=["c"] * 4, name="table_runs")
    text(s, x + 0.25, y0 + 2.2, cw - 0.45, 0.95, "四个核心进程全程在线，无中途退出、无 CRITICAL；驱动层按设定故障率注入。",
         size=12, spacing=1.2, name="card_body")
    x = M + cw + 0.3
    y0 = card(s, x, cy, cw, ch, "复核成功率")
    stat(s, x + 0.25, y0 + 0.02, cw - 0.45, "7 / 7", "真权重两轮（含 30 分钟长稳），读数全部产出", color=GREEN, vsize=30)
    stat(s, x + 0.25, y0 + 1.02, cw - 0.45, "83.3 %", "三轮 180 s 中位（83.3 / 66.7 / 85.7 %）", color=NAVY, vsize=30)
    text(s, x + 0.25, y0 + 2.05, cw - 0.45, 1.05, "这三轮里没成功的复核都判为证据不足、交人工，原因是注入的指令丢包、对焦失败或目标出框。",
         size=12, spacing=1.2, name="card_body")
    x = M + 2 * (cw + 0.3)
    y0 = card(s, x, cy, cw, ch, "断网 75 s")
    for i, (v, l) in enumerate([("4", "证据包"), ("51", "落盘文件"), ("22", "最多重试轮次")]):
        stat(s, x + 0.25 + i * 1.2, y0 + 0.02, 1.1, v, l, color=RED if i == 2 else NAVY, vsize=30)
    text(s, x + 0.25, y0 + 1.1, cw - 0.45, 1.95, [
        {"runs": ["4 个证据包全部留在本地，没有包被放弃"], "bullet": True, "gap": 6},
        {"runs": ["重试轮次 7、12、17、22，恢复后续传"], "bullet": True}], size=12, spacing=1.2, name="card_body")
    sy = 5.3
    box(s, M, sy, W - 2 * M, 1.3, fill=BLUE_SOFT, radius=0.08, name="card")
    for i, (v, l) in enumerate([("59 项", "接口一致性校验全部通过"), ("557 条", "自动化测试用例"),
                                ("8.3 fps", "真权重巡航帧率：渲染占 61.8 ms，真机无此开销")]):
        stat(s, M + 0.45 + i * 4.15, sy + 0.14, 3.8, v, l, color=NAVY, vsize=28)
    footnote(s, "复现：python -m patrol.tools.run_all --seconds 1800（长稳）/ --seconds 300；断网测试：run_all --seconds 75 --no-cloud。感知处理段 P95 36.1 ms。", y=6.86)


def s21(s):
    content(s, L4_, "4.3 读数精度与误差来源")
    iw = 11.0
    _, ih = image(s, os.path.join(IMG, "repeat_final.png"), (W - iw) / 2, 1.06, w=iw, name="img_rep")
    cy = 1.06 + ih + 0.34
    ch = 7.08 - cy
    cw = (W - 2 * M - 0.3) / 2
    y0 = card(s, M, cy, cw, ch, "误差主要来自表盘贴图的像素采样")
    text(s, M + 0.25, y0 + 0.08, cw - 0.45, ch - 0.45, [
        {"runs": ["同一角度重复读 5 次，误差逐位相同（读数 0.4 处 5 次均为 +0.3792°），算法本身没有随机性"], "bullet": True, "gap": 6},
        {"runs": ["表盘贴图边长 100 / 150 / 240 px 时，折合 0.416 / 0.305 / 0.227 % FS，边长越大误差越小"], "bullet": True}],
         size=12.5, spacing=1.15, name="card_body")
    rx = M + cw + 0.3
    y0 = card(s, rx, cy, cw, ch, "判定标准")
    text(s, rx + 0.25, y0 + 0.08, cw - 0.45, ch - 0.45, [
        {"runs": ["重复性限值 9 月 10 日由 0.3 修订为 0.4 % FS，与线性度同级；43 次中 39 次合格"], "bullet": True, "gap": 5},
        {"runs": ["2450 次读数中 6 次偏离 2.0–14.7°，置信度都 ≤ 0.879（正常读数均为 1.000），可以全部剔除"], "bullet": True, "gap": 5},
        {"runs": ["没有为压低误差调整任何算法参数"], "bullet": True}], size=12.5, spacing=1.12, name="card_body")


def s23(s):
    content(s, L5_, "5.1 主要成果与创新点")
    lw = 5.15
    y0 = card(s, M, 1.3, lw, 5.62, "完成的工作")
    items = ["感知、任务、网关、上传四个边缘进程和云端台账，在虚拟配电室跑通检测到上传全流程",
             "L2、L3 指标在本机复现，L1 检测在本机端到端跑通",
             "五份 JSON Schema（ICD v2.1）、59 项一致性校验、557 条自动化测试",
             "三层安全防线，三条演示可当场执行",
             "两级检测器与 PaDiM 特征网络已转为 RK3576 模型，Linux 部署包已打好",
             "串口协议栈与假小车，真机驱动按配置切换"]
    text(s, M + 0.25, y0 + 0.16, lw - 0.45, 5.0, [{"runs": [it], "bullet": True, "gap": 14} for it in items],
         size=13.5, spacing=1.2, name="card_body")
    gx = M + lw + 0.32
    gw = (BODY_R - gx - 0.3) / 2
    gh = 2.66
    inno = [("01", "按像素密度定变焦倍率", "按目标当前像素密度算出变焦倍率，停车复核后再读数。", "实例 49 px → 139 px（2.81×）"),
            ("02", "按变焦倍率调度增益", "PID 输出乘以 θ/(W·z)，各倍率下环路增益一致。", "3× 超调 1.0 %，调节 1.10 s"),
            ("03", "四路模型加规则仲裁", "检测、读数、OCR、异常各管一件事，结论附逐条理由。", "L3 漏报 3.3 %，每条结论附理由"),
            ("04", "接口冻结与驱动抽象", "D3 评审后冻结 Schema；抽象接口隔离硬件，桩按设定故障率注入。", "59 项校验，557 条测试")]
    for i, (n, t, b, ev) in enumerate(inno):
        r_, c_ = divmod(i, 2)
        x = gx + c_ * (gw + 0.3)
        y = 1.3 + r_ * (gh + 0.3)
        box(s, x, y, gw, gh, fill=WHITE, line=EDGE, radius=0.08, name="card")
        box(s, x, y, 0.08, gh, fill=NAVY, rect=True, name="accent")
        text(s, x + 0.3, y + 0.14, 0.8, 0.5, n, size=24, bold=True, color=RED, name="inno_no")
        text(s, x + 0.3, y + 0.68, gw - 0.5, 0.36, t, size=14.5, bold=True, color=NAVY, name="inno_title")
        text(s, x + 0.3, y + 1.08, gw - 0.5, 0.86, b, size=12.5, spacing=1.2, name="inno_body")
        text(s, x + 0.3, y + gh - 0.74, gw - 0.5, 0.62, [{"runs": [B("实测", bold=True, color=GREEN, size=11)]},
                                                        {"runs": [B(ev, bold=True, color=GREEN, size=13)]}],
             spacing=1.05, anchor="b", name="inno_ev")


def s24(s):
    content(s, L5_, "5.2 实车部署与后续工作")
    tab(s, M, 1.08, "实车部署步骤")
    steps = [("模型转换", "检测器 FP16，L3 INT8", True), ("推理后端", "RKNN 与 ONNX 后端", True),
             ("标定表加载", "真机按标定表取读数先验", True), ("打包安装", "部署包一条命令装好", True),
             ("上板联调", "填串口，重算巡检距离", False)]
    bw = 2.2
    gap = (W - 2 * M - 5 * bw) / 4
    for i, (t, d, done) in enumerate(steps):
        x = M + i * (bw + gap)
        box(s, x, 1.58, bw, 1.42, fill=BLUE_SOFT, line=NAVY, line_w=0.9, radius=0.08, name="flow")
        circle_num(s, x + 0.16, 1.72, 0.36, str(i + 1), size=12)
        text(s, x + 0.62, 1.72, bw - 0.72, 0.36, t, size=13.5, bold=True, color=NAVY, anchor="m", name="step_title")
        text(s, x + 0.16, 2.2, bw - 0.3, 0.72,
             [{"runs": [B(d, size=12)], "gap": 3},
              {"runs": [B("已完成" if done else "待上板", size=11, bold=True, color=GREEN if done else RED)]}],
             spacing=1.12, name="step_desc")
        if i < 4:
            arrow(s, x + bw + 0.04, 2.29, x + bw + gap - 0.04, 2.29, color=NAVY, width=1.75)
    cy, ch = 3.46, 3.46
    cw = (W - 2 * M - 0.3) / 2
    y0 = card(s, M, cy, cw, ch, "已经做完的")
    text(s, M + 0.25, y0 + 0.12, cw - 0.45, ch - 0.5, [
        {"runs": ["部署包 103 MB：代码、配置、四个 RKNN 模型、安装脚本与系统服务"], "bullet": True, "gap": 14},
        {"runs": ["检测器 FP16 与 PC 端逐框一致；L3 INT8 判定与 PC 端相同"], "bullet": True, "gap": 14},
        {"runs": ["x86 Linux 上装包验证：接口校验 59 项、桩模式两轮跑通"], "bullet": True, "gap": 14},
        {"runs": ["串口协议栈用假小车逐字节走通，四个真机驱动已实现"], "bullet": True}], size=14.5, spacing=1.2, name="card_body")
    rx = M + cw + 0.3
    y0 = card(s, rx, cy, cw, ch, "上板之后要做的")
    text(s, rx + 0.25, y0 + 0.12, cw - 0.45, ch - 0.5, [
        {"runs": [B("实测 NPU 单帧耗时", bold=True, color=NAVY)], "bullet": True, "gap": 2},
        {"runs": [B("模拟器只证明转换没掉点，代表不了板上速度", size=13, color=MUTED)], "gap": 12, "indent": True},
        {"runs": [B("真实相机、串口、云台联调", bold=True, color=NAVY)], "bullet": True, "gap": 2},
        {"runs": [B("现场标定表按实测位置录入", size=13, color=MUTED)], "gap": 12, "indent": True},
        {"runs": [B("复核成功率提到 85 % 以上", bold=True, color=NAVY)], "bullet": True, "gap": 2},
        {"runs": [B("减少对焦失败和目标出框造成的证据不足", size=13, color=MUTED)], "gap": 12, "indent": True},
        {"runs": [B("条件式辅视角采集", bold=True, color=NAVY)], "bullet": True, "gap": 2},
        {"runs": [B("Schema 已预留字段，证据包格式不用改", size=13, color=MUTED)], "indent": True}],
         size=14.5, spacing=1.2, name="card_body")


def s25(s):
    content(s, L5_, "5.3 小组分工与协作")
    ph = {"bold": True, "color": RED}                       # 姓名待填
    rows = [["成员", "承担的模块", "主要产出与数据"],
            [("吴明哲（组长）", {"bold": True}),
             "系统集成与接口定义、主动复核状态机、安全网关、云台伺服与变焦增益调度、"
             "指针读数与标定、证据链与云端、虚拟配电室、RK3576 部署与全系统测试",
             "ICD v2.1 与五份 Schema、59 项一致性校验；读数与云台各三项指标；557 条测试；102.9 MB 部署包"],
            [("【姓名】", ph),
             "L1 目标检测：数据集制备与防泄漏检查、两级 YOLO11 混合微调、ONNX 导出与检出评测",
             "mAP50 0.9941，漏检率 0.168 % / 0.185 %，单帧 22 / 43 ms"],
            [("【姓名】", ph),
             "L2 分割方案：U-Net 指针与刻度分割的数据集、训练与评测，以及与几何法的读数比选实验",
             "指针 IoU 0.812；比选结论：读数不启用学习法"],
            [("【姓名】", ph),
             "L3 未知异常检测：PaDiM 与 EfficientAD 训练评测、正常样本集增广、特征网络 ONNX 与 RKNN 转换",
             "误报 3.8 %、漏报 3.3 %；INT8 上板判定零翻转"]]
    table(s, M, 1.12, [2.05, 5.6, 4.85], rows, row_h=0.74, size=11.5, name="table_roles")
    y0 = card(s, M, 5.08, W - 2 * M, 1.62, "协作方式")
    text(s, M + 0.25, y0 + 0.12, W - 2 * M - 0.5, 1.2, [
        {"runs": [B("接口先冻结再写代码　", bold=True, color=NAVY), "ICD 评审 24 条决议全部落地，四个进程并行开发，联调按报文对齐"], "bullet": True, "gap": 6},
        {"runs": [B("交付按人分目录　", bold=True, color=NAVY), "每个数都标证据等级：实测 / 桩构造 / 交付方报告 / 未验证"], "bullet": True, "gap": 6},
        {"runs": [B("组长逐条复跑三条通路　", bold=True, color=NAVY), "两条一开始复现不出来，都定位到具体代码并修好，其中分割的指针 IoU 修完从 0.778 升到 0.812"], "bullet": True}],
         size=12.5, spacing=1.2, name="card_body")
    footnote(s, [{"runs": [B("组员姓名与各自的工作比例待填。", color=RED, bold=True),
                           "分工口径与小组报告封面的成员名单一致。"]}], y=6.96)


BUILDERS = {3: s03, 4: s04, 6: s06, 7: s07, 8: s08, 9: s09, 10: s10, 12: s12, 13: s13, 14: s14, 15: s15, 16: s16,
            17: s17, 19: s19, 20: s20, 21: s21, 23: s23, 24: s24, 25: s25}


def fix_footer(prs):
    """模板页码与日期是字段，缓存值还是模板里的 13 / 2026/1/12。WPS 与缩略图用缓存值，所以写成实际值。"""
    import copy as _copy
    for k, sl in enumerate(prs.slides, 1):
        for sh in sl.shapes:
            if not sh.is_placeholder:
                continue
            for fld in list(sh._element.iter(qn("a:fld"))):
                kind = fld.get("type", "")
                if kind == "slidenum":
                    fld.find(qn("a:t")).text = str(k)
                elif kind.startswith("datetime"):
                    r = fld.makeelement(qn("a:r"), {})
                    rpr = fld.find(qn("a:rPr"))
                    if rpr is not None:
                        r.append(_copy.deepcopy(rpr))
                    t = r.makeelement(qn("a:t"), {})
                    t.text = "2026 年 9 月"
                    r.append(t)
                    fld.addprevious(r)
                    fld.getparent().remove(fld)


def fix_photo_footers(prs):
    """目录页与结束页的页脚压在照片上：日期去掉，结束页页码去掉，目录页页码改深蓝。"""
    for (tpl, kind), sl in zip(MAP, prs.slides):
        if kind not in ("toc", "end"):
            continue
        for sh in list(sl.shapes):
            if not sh.is_placeholder:
                continue
            is_num = any(f.get("type") == "slidenum" for f in sh._element.iter(qn("a:fld")))
            if kind == "end" or not is_num:
                sh._element.getparent().remove(sh._element)
                continue
            for fld in sh._element.iter(qn("a:fld")):
                rpr = fld.find(qn("a:rPr"))
                if rpr is None:
                    rpr = fld.makeelement(qn("a:rPr"), {"lang": "zh-CN"})
                    fld.insert(0, rpr)
                for old in rpr.findall(qn("a:solidFill")):
                    rpr.remove(old)
                sf = rpr.makeelement(qn("a:solidFill"), {})
                sf.append(sf.makeelement(qn("a:srgbClr"), {"val": NAVY}))
                ln = rpr.find(qn("a:ln"))
                (ln.addnext(sf) if ln is not None else rpr.insert(0, sf))
                rpr.set("b", "1")


def stage_b():
    prs = Presentation(SKELETON)
    assert len(prs.slides) == 26, len(prs.slides)
    for k, ((tpl, kind), dst) in enumerate(zip(MAP, prs.slides), 1):
        if kind == "cover":
            do_cover(dst)
        elif kind == "end":
            do_end(dst)
        elif kind == "toc":
            do_toc(dst)
        else:
            BUILDERS[k](dst)
    import notes
    notes.apply(prs)
    fix_footer(prs)
    fix_photo_footers(prs)
    cp = prs.core_properties
    cp.title = "基于 RK3576 边缘计算的无人车主动式 AI 巡检系统 · 最终答辩"
    cp.author = "吴明哲"
    prs.save(OUT)
    print("已生成", OUT, "%.1f MB" % (os.path.getsize(OUT) / 1e6))


if __name__ == "__main__":
    if "b" not in sys.argv[1:]:
        stage_a()
    stage_b()
