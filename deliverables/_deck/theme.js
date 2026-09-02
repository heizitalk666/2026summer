// 中期答辩 PPT —— 配色、版式与可复用组件
//
// 母题：琥珀圆环（呼应系统要读的压力表表盘），每个内容页标题左侧一个，内嵌章节号。
// 结构：深—浅—深三明治（封面/方法论/结束页深底，内容页浅底）。
// 纪律：不用标题下划线、不用色条色带。

const C = {
  ink:      "171C24",   // 深底
  inkSoft:  "222A35",   // 深底上的卡片
  card:     "F2F5F8",   // 浅底上的卡片
  cardEdge: "DCE3EA",
  white:    "FFFFFF",
  text:     "1A2028",
  muted:    "6B7A8A",
  mutedOnInk: "9AA9B8",
  amber:    "E8940C",   // 唯一强调色
  amberSoft:"FBEAC9",
  steel:    "35617D",
  steelSoft:"D9E4EC",
  green:    "2E9E6B",
  greenSoft:"D7EFE3",
  red:      "C0392B",
  redSoft:  "F7DDD9",
};

const F = "Microsoft YaHei";
const W = 13.333, H = 7.5;          // LAYOUT_WIDE
const M = 0.62;                      // 页边距

// ---------------------------------------------------------------- 版式组件

/** 深色页背景 */
function darkBg(s) {
  s.background = { color: C.ink };
}

/** 内容页页眉：琥珀圆环 + 章节号 + 标题 + 可选副标题。返回正文起始 y。 */
function head(s, num, title, sub) {
  s.addShape("ellipse", {
    x: M, y: 0.42, w: 0.54, h: 0.54,
    fill: { color: C.white }, line: { color: C.amber, width: 2.25 },
  });
  s.addText(String(num), {
    x: M, y: 0.42, w: 0.54, h: 0.54, align: "center", valign: "middle",
    fontFace: F, fontSize: 15, bold: true, color: C.amber, isTextBox: true, margin: 0,
  });
  s.addText(title, {
    x: M + 0.76, y: 0.36, w: W - M * 2 - 0.76, h: 0.5,
    fontFace: F, fontSize: 27, bold: true, color: C.text,
    isTextBox: true, margin: 0, valign: "middle",
  });
  if (sub) {
    s.addText(sub, {
      x: M + 0.76, y: 0.90, w: W - M * 2 - 0.76, h: 0.34,
      fontFace: F, fontSize: 13, color: C.muted, isTextBox: true, margin: 0, valign: "middle",
    });
    return 1.42;
  }
  return 1.14;
}

/** 页脚：左侧说明 + 右侧页码 */
function foot(s, note, page) {
  if (note) {
    s.addText(note, {
      x: M, y: H - 0.52, w: W - M * 2 - 0.9, h: 0.3,
      fontFace: F, fontSize: 9.5, color: C.muted, isTextBox: true, margin: 0, valign: "middle",
    });
  }
  s.addText(String(page), {
    x: W - M - 0.6, y: H - 0.52, w: 0.6, h: 0.3, align: "right",
    fontFace: F, fontSize: 10, color: C.muted, isTextBox: true, margin: 0, valign: "middle",
  });
}

/** 圆角卡片 */
function card(s, x, y, w, h, fill) {
  s.addShape("roundRect", {
    x, y, w, h, rectRadius: 0.07,
    fill: { color: fill || C.card },
    line: { color: fill ? fill : C.cardEdge, width: 0.75 },
  });
}

/** 大数字统计块：数值 + 单位 + 标签 */
function stat(s, x, y, w, value, label, color, unit) {
  const col = color || C.text;
  s.addText(
    unit ? [{ text: value, options: { fontSize: 34, bold: true, color: col } },
            { text: " " + unit, options: { fontSize: 13, bold: true, color: col } }]
         : [{ text: value, options: { fontSize: 34, bold: true, color: col } }],
    { x, y, w, h: 0.62, fontFace: F, isTextBox: true, margin: 0, valign: "bottom" });
  s.addText(label, {
    x, y: y + 0.64, w, h: 0.34,
    fontFace: F, fontSize: 11.5, color: C.muted, isTextBox: true, margin: 0, valign: "top",
  });
}

/** 小标签（状态徽章） */
function badge(s, x, y, w, text, fg, bg) {
  s.addShape("roundRect", {
    x, y, w, h: 0.3, rectRadius: 0.14, fill: { color: bg }, line: { color: bg, width: 0.5 },
  });
  s.addText(text, {
    x, y, w, h: 0.3, align: "center", valign: "middle",
    fontFace: F, fontSize: 10, bold: true, color: fg, isTextBox: true, margin: 0,
  });
}

/** 段落标题（卡片内） */
function cardTitle(s, x, y, w, text, color) {
  s.addText(text, {
    x, y, w, h: 0.32,
    fontFace: F, fontSize: 14.5, bold: true, color: color || C.text,
    isTextBox: true, margin: 0, valign: "middle",
  });
}

/** 项目符号列表 */
function bullets(s, x, y, w, h, items, size) {
  s.addText(items.map((t, i) => ({
    text: t,
    options: { bullet: true, breakLine: i !== items.length - 1 },
  })), {
    x, y, w, h,
    fontFace: F, fontSize: size || 12.5, color: C.text,
    isTextBox: true, margin: 0, paraSpaceAfter: 7, lineSpacing: 18,
  });
}

/** 表格（统一风格） */
function table(s, rows, opts) {
  s.addTable(rows, Object.assign({
    fontFace: F, fontSize: 11.5, color: C.text,
    border: { type: "solid", color: C.cardEdge, pt: 0.75 },
    align: "left", valign: "middle", autoPage: false,
  }, opts));
}

/** 表头单元格 */
function th(text, opts) {
  return Object.assign({
    text, options: { bold: true, color: C.white, fill: { color: C.steel }, fontSize: 11.5, align: "center" },
  }, opts || {});
}

/** 普通单元格 */
function td(text, o) {
  return { text, options: Object.assign({ fontSize: 11.5 }, o || {}) };
}

module.exports = { C, F, W, H, M, darkBg, head, foot, card, stat, badge, cardTitle, bullets, table, th, td };
