// 封面与目录
const T = require("./theme");
const { C, F, W, H, M } = T;
const P = require("./newpages");

module.exports = function (pres, IMG) {

  // ============================================================ 1 封面
  {
    const s = T.slide(pres);
    T.darkBg(s);
    // 母题：右侧一组同心圆环（表盘）
    s.addShape("ellipse", { x: 9.05, y: 1.15, w: 3.95, h: 3.95,
      fill: { color: C.ink }, line: { color: C.amber, width: 1.5 } });
    s.addShape("ellipse", { x: 9.52, y: 1.62, w: 3.01, h: 3.01,
      fill: { color: C.ink }, line: { color: C.steel, width: 1 } });
    // 指针：以轴心为中心画（穿过轴心的针 + 配重尾，正是本项目表计的样子）；
    // pptxgenjs 绕形状中心旋转，所以中心必须和轴心重合，否则针会「飘」在轴心外。
    s.addShape("rect", { x: 11.0025, y: 2.525, w: 0.045, h: 1.20,
      fill: { color: C.amber }, line: { color: C.amber, width: 0.5 }, rotate: 34 });
    s.addShape("ellipse", { x: 10.855, y: 2.955, w: 0.34, h: 0.34,
      fill: { color: C.amber }, line: { color: C.amber, width: 1 } });

    s.addText("中期答辩", {
      x: M, y: 1.30, w: 7.6, h: 0.4, fontFace: F, fontSize: 15, bold: true,
      color: C.amber, charSpacing: 3, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("基于 RK3576 边缘计算的\n无人车主动式 AI 巡检系统", {
      x: M, y: 1.85, w: 8.0, h: 1.85, fontFace: F, fontSize: 37, bold: true,
      color: C.white, isTextBox: true, margin: 0, valign: "top", lineSpacing: 48 });
    s.addText("配电室设备状态视觉测控 · 停车对准变焦，把表盘从 50 px 放大到 150 px 再读", {
      x: M, y: 3.86, w: 8.0, h: 0.42, fontFace: F, fontSize: 14,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "middle" });

    const chips = [["21 100", "行代码"], ["501", "项测试"], ["4", "路模型"], ["51", "项接口校验"]];
    chips.forEach(([v, k], i) => {
      const x = M + i * 1.98;
      s.addText(v, { x, y: 4.62, w: 1.8, h: 0.5, fontFace: F, fontSize: 25, bold: true,
        color: C.amber, isTextBox: true, margin: 0, valign: "bottom" });
      s.addText(k, { x, y: 5.14, w: 1.8, h: 0.3, fontFace: F, fontSize: 11,
        color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top" });
    });

    s.addText("四人小组 · 甲 L1 检测 / 乙 L2 分割 / 丙 L3 异常 / 组长 系统与交付        2026 年 9 月", {
      x: M, y: 6.55, w: W - M * 2, h: 0.34, fontFace: F, fontSize: 11,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "middle" });
    s.addNotes("开场：本系统的做法是，巡航时以低算力扫描一遍，检出可疑目标后停车、对准、变焦、重新拍摄。这一步把表盘成像从 50 像素放大到 150 像素，读数精度才能达到 0.5 % FS 的要求。与「沿途录像、回去离线分析」的区别就在这里。硬件尚未到位，但整套系统可以在笔记本上完整运行，所有指标均为实测值。");
  }

  // ============================================================ 2 目录
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "目", "汇报提纲", "按研究背景、思路结构、方法与内容、总结创新、文献与不足五个部分展开");
    const secs = [
      ["一", "研究背景和现状", "配电室巡检的三项约束、国内外三类既有方案、问题的提出、研究目标与结果", "3 – 7"],
      ["二", "研究思路和结构", "四个设计决定、总体方案、系统结构、逻辑主线、研究内容的组织方式", "8 – 13"],
      ["三", "方法和研究内容", "两项支撑性方法、资料来源，以及九项研究内容的实施方式与实测数据", "14 – 33"],
      ["四", "总结与创新点", "工作量与质量、研究进度、阶段性结论、四条主要创新点", "34 – 40"],
      ["五", "参考文献与存在的不足", "三类参考资料及其处理方式、六项存在的不足、下一阶段计划", "41 – 45"],
    ];
    secs.forEach(([n, t, d, p], i) => {
      const y = y0 + 0.04 + i * 1.11;
      T.card(s, M, y, W - M * 2, 0.96);
      s.addText(n, { x: M + 0.3, y, w: 0.9, h: 0.96, fontFace: F, fontSize: 26, bold: true,
        color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(t, { x: M + 1.24, y: y + 0.12, w: 8.6, h: 0.36, fontFace: F, fontSize: 16,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 1.24, y: y + 0.50, w: 9.4, h: 0.34, fontFace: F, fontSize: 11,
        color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
      s.addText("P " + p, { x: W - M - 1.5, y, w: 1.2, h: 0.96, align: "right",
        fontFace: F, fontSize: 12, color: C.steel, isTextBox: true, margin: 0, valign: "middle" });
    });
    T.foot(s, null);
    s.addNotes("提纲四部分。时间紧的话，重点讲 01 的像素密度立论和 03 的三条实测结论。");
  }

};
