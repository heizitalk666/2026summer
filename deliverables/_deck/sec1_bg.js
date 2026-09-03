// 一、研究背景和现状
const T = require("./theme");
const { C, F, W, H, M } = T;
const P = require("./newpages");

module.exports = function (pres, IMG) {

  T.divider(pres, "一", "研究背景和现状", "本节回答：为什么做这个课题？意义在哪？目标是什么？取得了什么结果？", ["配电室巡检的三项约束，以及它们为何互相冲突", "国内外三类既有方案能解决什么、回避了什么", "问题的提出：像素密度与读数精度的定量关系", "四项研究目标与本阶段的实测结果", "本阶段结论：六项量化要求中五项达成，重复性一项压线未达标"]);

  // ============================================================ 3 课题与难点
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究背景：配电室巡检面临的三项约束", "任务书三项要求，每一项都指向同一个矛盾");
    const items = [
      ["看得清", "表计读数误差 ≤ 0.5 % FS", "5 m 处 1× 变焦时表盘成像只有 50 px，指针无法分辨", C.red],
      ["跑得完", "单次巡检 ≤ 30 min", "靠近、放大、多次拍摄都会增加单个航点的停留时间", C.amber],
      ["不误动作", "不能因 AI 误判撞到设备", "模型存在误判，而车辆在运动，停车决策不能由模型直接触发", C.steel],
    ];
    items.forEach(([t, spec, why, col], i) => {
      const x = M + i * 4.13;
      T.card(s, x, y0, 3.87, 2.92);
      s.addShape("ellipse", { x: x + 0.28, y: y0 + 0.28, w: 0.42, h: 0.42,
        fill: { color: col }, line: { color: col, width: 1 } });
      s.addText(String(i + 1), { x: x + 0.28, y: y0 + 0.28, w: 0.42, h: 0.42, align: "center",
        valign: "middle", fontFace: F, fontSize: 12, bold: true, color: C.white, isTextBox: true, margin: 0 });
      s.addText(t, { x: x + 0.84, y: y0 + 0.26, w: 2.8, h: 0.44, fontFace: F, fontSize: 18,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(spec, { x: x + 0.28, y: y0 + 0.86, w: 3.3, h: 0.34, fontFace: F, fontSize: 12,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(why, { x: x + 0.28, y: y0 + 1.24, w: 3.32, h: 1.44, fontFace: F, fontSize: 11.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 16 });
    });

    T.card(s, M, y0 + 3.14, W - M * 2, 2.06, C.ink);
    s.addText("三项要求互相冲突，这是本课题的主要难点", {
      x: M + 0.42, y: y0 + 3.40, w: 11.2, h: 0.4, fontFace: F, fontSize: 16, bold: true,
      color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("全程低速高分辨率拍摄能满足精度但超出时间预算；全程高速扫描能满足时间但达不到精度。" +
      "本方案把两者分开：巡航期用轻量检测模型以 30 Hz 扫描，只有检出可疑目标后才停车、对准、" +
      "变焦、重新拍摄，时间开销只发生在需要复核的航点上。是否停车由状态机的抑制规则与安全网关判定，" +
      "模型只提供候选，不直接控制执行器。", {
      x: M + 0.42, y: y0 + 3.90, w: 11.2, h: 1.10, fontFace: F, fontSize: 12.5,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 19 });
    T.foot(s, "指标出处：设计方案书 §2.2 表 2-2 / 测控系统综合实训课题任务书");
    s.addNotes("本页要讲清三项要求之间的冲突。如果评审问为什么不直接换更大的模型，答案是：50 像素的成像里本来就没有足够信息，信息在光学环节已经丢失，只能通过变焦重新采集。");
  }

  P.status(pres, IMG);

  // ============================================================ 4 核心立论：像素密度
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "问题的提出：像素密度决定读数精度", "同一块压力表在巡航态与复核态下的成像对比");
    s.addImage({ path: IMG + "/zoom_compare.png", x: M, y: y0, w: 12.09, h: 3.92 });

    const st = [["50.0 px", "巡航态 1× 实测框宽", C.red],
                ["150.0 px", "复核态 3× 实测框宽", C.green],
                ["0.4 %", "实测与针孔公式的偏差", C.steel]];
    st.forEach(([v, k, col], i) => T.stat(s, M + i * 3.05, y0 + 4.12, 2.9, v, k, col));

    T.card(s, M + 9.3, y0 + 4.04, 2.79, 1.16, C.amberSoft);
    s.addText("公式算出 49.8 / 149.5 px\n渲染器与光学模型自洽", {
      x: M + 9.55, y: y0 + 4.16, w: 2.4, h: 0.92, fontFace: F, fontSize: 11.5, bold: true,
      color: "8A5A05", isTextBox: true, margin: 0, valign: "middle", lineSpacing: 17 });
    T.foot(s, "复现：python -m patrol.tools.viewer --demo-zoom --out out/   ·   光学公式集中在 patrol/scene/optics.py");
    s.addNotes("左侧巡航态 50 像素，指针无法分辨；右侧 3 倍变焦后 150 像素，刻度可读。要强调的是实测框宽与针孔投影公式的计算值相差 0.4 %，说明虚拟场景在光学上与公式自洽，因此在其上测得的精度指标可以作为依据。");
  }

  P.goals(pres, IMG);

};
