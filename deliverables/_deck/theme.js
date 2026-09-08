// 中期答辩 PPT —— 版式与组件
//
// 版式取自学院模板（C424_PPT_2024）：顶部蓝色横幅、右上校徽、底部细条、
// 左上「一.标题」节号块、居中「1.1 二级标题」圆角块。几何尺寸与模板逐项对齐。
// 页码与章节号自动递增，插页删页不必改别处。

const path = require("path");

const C = {
  navy:     "0B3C6B",   // 模板主色（深蓝）
  blue:     "1667B0",   // 次级蓝
  blueSoft: "E4EEF7",
  card:     "F4F7FA",
  cardEdge: "D5E1EC",
  white:    "FFFFFF",
  text:     "1A2430",
  muted:    "5D6E7E",
  mutedOnInk: "A9BACB",
  red:      "C0202B",   // 模板用红色作强调
  redSoft:  "FBE3E4",
  amber:    "D98200",
  amberSoft:"FBEBD2",
  green:    "1E7A४6".replace("४",""),   // 1E7A46
  greenSoft:"DCEFE4",
  ink:      "0B2A47",
};
C.green = "1E7A46";

const F = "Microsoft YaHei";
const W = 13.333, H = 7.5;
const M = 0.42;                      // 模板内容区左右边距
const BODY_TOP = 1.06;               // 横幅下沿
const BODY_BOT = 7.18;               // 底条上沿

let PAGE = 0, SEC = 0, TPL = null;
function setImgDir(d) { TPL = path.join(d, "tpl"); }
function slide(pres) { PAGE += 1; return pres.addSlide(); }
function pageNow() { return PAGE; }

/** 模板页眉：横幅 + 校徽 + 底条 + 节号 + 二级标题块。返回正文起始 y。 */
function head(s, sec, title, sub) {
  s.addImage({ path: TPL + "/banner.jpg", x: -0.02, y: -0.02, w: 13.35, h: 0.89 });
  s.addImage({ path: TPL + "/logo.png",   x: 10.72, y: 0.13, w: 2.53, h: 0.65 });
  s.addImage({ path: TPL + "/strip.png",  x: 0, y: 7.32, w: 13.33, h: 0.18 });
  s.addText(sec, {
    x: 0.10, y: 0.10, w: 2.30, h: 0.68, fontFace: F, fontSize: 15, bold: true,
    color: C.white, isTextBox: true, margin: 0, valign: "middle" });
  // 二级标题：模板是居中的圆角块
  s.addShape("roundRect", { x: 3.05, y: 0.20, w: 7.30, h: 0.50, rectRadius: 0.10,
    fill: { color: C.white }, line: { color: C.white, width: 0.75 } });
  s.addText(title, {
    x: 3.15, y: 0.20, w: 7.10, h: 0.50, align: "center", valign: "middle",
    fontFace: F, fontSize: 15.5, bold: true, color: C.navy, isTextBox: true, margin: 0 });
  if (sub) {
    // sub 传数组 [采用什么, 为什么] 时排成「决定行」：绿色的结论在前，理由跟在后面。
    // 第三部分每一页都用这种写法，翻到哪一页，先看到的都是选了什么、为什么选它。
    const body = Array.isArray(sub) ? [
      { text: "采用　", options: { bold: true, color: C.green, fontSize: 11.5 } },
      { text: sub[0], options: { bold: true, color: C.green, fontSize: 12 } },
      { text: "　　理由　", options: { bold: true, color: C.muted, fontSize: 11.5 } },
      { text: sub[1], options: { color: C.muted, fontSize: 11.5 } },
    ] : sub;
    s.addText(body, { x: M, y: BODY_TOP - 0.02, w: W - M * 2, h: 0.32,
      fontFace: F, fontSize: 12, color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
    return BODY_TOP + 0.38;
  }
  return BODY_TOP + 0.04;
}

/** 页脚：左侧说明 + 右侧页码（贴在底条之上） */
function foot(s, note) {
  if (note) s.addText(note, { x: M, y: H - 0.44, w: W - M * 2 - 0.9, h: 0.26,
    fontFace: F, fontSize: 9.8, color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
  s.addText(String(PAGE), { x: W - M - 0.62, y: H - 0.44, w: 0.6, h: 0.26, align: "right",
    fontFace: F, fontSize: 10.2, color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
}

/** 目录页 / 分节页：模板的样式是左图右列表，当前节高亮 */
const SECTIONS = [
  ["一", "研究背景与需求"],
  ["二", "关键技术难题与研究目标"],
  ["三", "研究内容与技术路线"],
  ["四", "项目特色与创新思路"],
  ["五", "研究计划与预期成果"],
];
function toc(pres, active, note) {
  const s = slide(pres);
  s.background = { color: C.blueSoft };
  s.addImage({ path: TPL + "/banner.jpg", x: -0.02, y: -0.02, w: 13.35, h: 0.89 });
  s.addImage({ path: TPL + "/logo.png",   x: 10.72, y: 0.13, w: 2.53, h: 0.65 });
  s.addImage({ path: TPL + "/strip.png",  x: 0, y: 7.32, w: 13.33, h: 0.18 });
  s.addText("目　录", { x: 0.10, y: 0.10, w: 2.60, h: 0.68, fontFace: F, fontSize: 17,
    bold: true, color: C.white, isTextBox: true, margin: 0, valign: "middle" });
  s.addImage({ path: TPL + "/toc_photo.jpg", x: M, y: 1.32, w: 5.20, h: 4.90 });
  SECTIONS.forEach(([no, t], i) => {
    const y = 1.42 + i * 0.98, on = (i + 1) === active;
    s.addShape("rect", { x: 6.10, y, w: 0.72, h: 0.74,
      fill: { color: on ? C.navy : "C9D6E2" }, line: { color: on ? C.navy : "C9D6E2", width: 0.5 } });
    s.addText(no, { x: 6.10, y, w: 0.72, h: 0.74, align: "center", valign: "middle",
      fontFace: F, fontSize: 19, bold: true, color: C.white, isTextBox: true, margin: 0 });
    s.addShape("rect", { x: 6.90, y, w: 5.98, h: 0.74,
      fill: { color: on ? C.white : "DCE6EF" }, line: { color: on ? C.navy : "DCE6EF", width: on ? 1 : 0.5 } });
    s.addText(t, { x: 7.18, y, w: 5.60, h: 0.74, fontFace: F, fontSize: on ? 16 : 14.5,
      bold: on, color: on ? C.navy : "7C8FA1", isTextBox: true, margin: 0, valign: "middle" });
  });
  foot(s, note);
  return s;
}

function card(s, x, y, w, h, fill) {
  s.addShape("roundRect", { x, y, w, h, rectRadius: 0.05,
    fill: { color: fill || C.card }, line: { color: fill ? fill : C.cardEdge, width: 0.75 } });
}
function stat(s, x, y, w, value, label, color) {
  s.addText(value, { x, y, w, h: 0.54, fontFace: F, fontSize: 28, bold: true,
    color: color || C.navy, isTextBox: true, margin: 0, valign: "bottom" });
  s.addText(label, { x, y: y + 0.56, w, h: 0.30, fontFace: F, fontSize: 11.2,
    color: C.muted, isTextBox: true, margin: 0, valign: "top" });
}
function cardTitle(s, x, y, w, text, color) {
  s.addText(text, { x, y, w, h: 0.30, fontFace: F, fontSize: 13.5, bold: true,
    color: color || C.navy, isTextBox: true, margin: 0, valign: "middle" });
}
function table(s, rows, opts) {
  s.addTable(rows, Object.assign({
    fontFace: F, fontSize: 11.2, color: C.text,
    border: { type: "solid", color: C.cardEdge, pt: 0.75 },
    align: "left", valign: "middle", autoPage: false }, opts));
}
function th(text) {
  return { text, options: { bold: true, color: C.white, fill: { color: C.navy },
    fontSize: 11.2, align: "center" } };
}
function td(text, o) { return { text, options: Object.assign({ fontSize: 10.8 }, o || {}) }; }

module.exports = { C, F, W, H, M, BODY_TOP, BODY_BOT, SECTIONS,
  setImgDir, slide, pageNow, head, foot, toc, card, stat, cardTitle, table, th, td };
