// 补充成果页：甲的分类表现、丙的误报漏报对比、证据包与复核增益
const T = require("./theme");
const { C, F, W, H, M } = T;

module.exports = function (pres, IMG) {

  // ==================================================== 甲 L1 分类表现
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "甲 · L1：三类状态量的分类表现",
      "归一化混淆矩阵与 PR 曲线。识别对象是任务书定的三类状态量，不是外观缺陷");
    s.addImage({ path: IMG + "/l1_cm.png", x: M, y: y0, w: 5.98, h: 4.48 });

    T.card(s, M + 6.24, y0, 5.85, 2.10);
    T.cardTitle(s, M + 6.48, y0 + 0.18, 5.3, "识别对象为什么是这三类", C.steel);
    s.addText("方案书 §6.2.1 的调研结论是：室内配电室的设备外观缺陷（渗漏油、呼吸器变色、积水）" +
      "几乎没有公开可用的标注数据，而设备状态量（表计读数、指示灯、开关位置）有配套数据集。" +
      "识别对象因此定为三类状态量。这也是差异清单 A2 把 ICD 原定的 OIL_LEAK 换成 SWITCH_HANDLE 的理由。\n\n" +
      "外观缺陷这一路没有放弃，改由 L3 非监督异常承接：它只需要正常样本，正好绕开标注数据不可得的约束。", {
      x: M + 6.48, y: y0 + 0.56, w: 5.34, h: 1.44, fontFace: F, fontSize: 10.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });

    T.card(s, M + 6.24, y0 + 2.30, 5.85, 1.42, C.card);
    T.cardTitle(s, M + 6.48, y0 + 2.46, 5.3, "两级模型的分工", C.steel);
    s.addText("巡航级追求召回：置信度阈值压到 0.25，宁可多报也不漏检，误报交给复核环节消解。\n" +
      "复核级追求准确：阈值提到 0.60，在放大后的画面上重新判定，并输出 Δconf 作为复核增益的度量。", {
      x: M + 6.48, y: y0 + 2.84, w: 5.34, h: 0.80, fontFace: F, fontSize: 10.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });

    T.card(s, M + 6.24, y0 + 3.92, 5.85, 0.80, C.amberSoft);
    s.addText("复核级模型明天上午补入，届时本页补充两级对比与 Δconf 的实测分布", {
      x: M + 6.48, y: y0 + 3.92, w: 5.34, h: 0.80, fontFace: F, fontSize: 10.5, bold: true,
      color: "8A5A05", isTextBox: true, margin: 0, valign: "middle", lineSpacing: 14 });
    T.foot(s, "识别对象：压力表读数 / 指示灯颜色 / 开关分合位。外观缺陷由 L3 非监督异常承接");
    s.addNotes("本页说明识别对象为什么定为三类状态量，而不是外观缺陷：由公开标注数据的可得性决定。外观缺陷这一路改由 L3 用非监督方法承接。两级模型的阈值分工也需要说明：巡航级保召回，复核级保准确。");
  }

  // ==================================================== 丙 L3 误报漏报对比
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "丙 · L3：四方案的误报与漏报对比",
      "同一批样本、同一阈值 0.55 下的直接比较");
    s.addImage({ path: IMG + "/l3_compare.png", x: M, y: y0, w: 6.30, h: 4.48 });

    const pts = [
      ["统计法为什么漏报高", C.steel,
       "它用马氏距离衡量偏离，只对整体分布的偏移敏感。异物遮挡只改变局部区域，" +
       "在全局统计量上的体现很弱，因此 120 张异常样本里有 109 张没有越过阈值。"],
      ["EfficientAD 为什么倒挂", C.red,
       "简化蒸馏在正常样本上过拟合后，学生网络对异常区域的重建误差反而更小，" +
       "导致异常样本得分低于正常样本。这不是调整超参数能解决的，属于方法与数据规模不匹配。"],
      ["全协方差为什么有效", C.green,
       "对角版本假设特征通道之间互相独立，忽略了通道间的相关性。" +
       "异物带来的正是通道间相关结构的改变，所以保留完整协方差矩阵后漏报从 48.3 % 降到 3.3 %，" +
       "代价是权重从 3.1 MB 增加到约 44 MB。"],
    ];
    pts.forEach(([t, col, d], i) => {
      const y = y0 + i * 1.62;
      T.card(s, M + 6.56, y, 5.53, 1.50);
      s.addText(t, { x: M + 6.80, y: y + 0.12, w: 5.05, h: 0.32, fontFace: F, fontSize: 12.5,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 6.80, y: y + 0.46, w: 5.07, h: 0.96, fontFace: F, fontSize: 10,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 13.5 });
    });
    T.foot(s, "三种方法的失败原因各不相同，因此比选结论不是「学习法更好」，而是「哪一种假设与本场景的数据匹配」");
    s.addNotes("本页说明三种方法结果不同的原因。统计法用马氏距离衡量全局偏离，对局部异常不敏感；EfficientAD 的简化蒸馏与本项目的数据规模不匹配，出现分数倒挂；全协方差版本之所以有效，是因为异物改变的正是特征通道之间的相关结构。");
  }

  // ==================================================== 证据包与复核增益
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "证据包：主动复核的效果由三个指标度量",
      "每次复核产出一个证据包，包含 before 与 after 两帧及其完整推理过程");
    const rows = [[T.th("指标"), T.th("定义"), T.th("目标"), T.th("实测"), T.th("说明")]];
    [["像素密度比", "p_after / p_before", "> 1.5", "1.9 至 2.3",
      "复核使目标成像放大的倍数。等于 0 表示 before 记录缺失，该包不计入成功"],
     ["Δconf", "after.confidence − before.confidence", "> +0.25（真缺陷组）", "+0.44",
      "必须按 verdict 分组统计。误报组为负值是正常的，混在一起算均值会接近零"],
     ["复核成功率", "verify_success 为真的包占比", "> 85 %", "75 至 83 %",
      "未达标。主因是证据包偶发配对失败，已定位未修复"],
    ].forEach(r => rows.push([
      T.td(r[0], { bold: true, fontSize: 10.5 }),
      T.td(r[1], { fontSize: 9.5, color: C.steel }),
      T.td(r[2], { align: "center", fontSize: 10 }),
      T.td(r[3], { align: "center", bold: true, fontSize: 10.5, color: r[0] === "复核成功率" ? C.red : C.green }),
      T.td(r[4], { fontSize: 9.5, color: C.muted })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [1.42, 2.42, 1.52, 1.20, 5.53], rowH: 0.72 });

    T.card(s, M, y0 + 2.46, 5.90, 2.52, C.ink);
    s.addText("证据包目录结构", { x: M + 0.30, y: y0 + 2.60, w: 5.3, h: 0.30, fontFace: F,
      fontSize: 12.5, bold: true, color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("evidence/<run_id>/<event_id>/\n" +
      "    manifest.json      结论、增益、时间线、文件清单\n" +
      "    fusion.json        四路模型各自的输出与仲裁理由\n" +
      "    mission_ctx.json   状态机过程与每个状态的耗时\n" +
      "    cruise_frame.jpg   巡航态原图\n" +
      "    verify_frame.jpg   复核态原图\n" +
      "    verify_roi.jpg     放大后的读数区域\n" +
      "    anomaly_heatmap.png  L3 异常热力图", {
      x: M + 0.30, y: y0 + 2.96, w: 5.32, h: 1.92, fontFace: "Courier New", fontSize: 8.8,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 12.5 });

    const disc = [
      ["统计口径由代码强制，不依赖人工遵守", "cloud/db.py 的 gain_stats() 强制按 verdict 分组，" +
       "并单独给出真缺陷组的均值。run_all 的小结与台账网页上都印着这条提醒。"],
      ["未确认上传的证据永不自动删除", "磁盘保留策略是全仓库唯一会删文件的地方。" +
       "超出配额时如实上报并交人处理，不静默丢弃。"],
      ["上传不阻塞主循环", "上传队列独立于感知与任务的主循环，" +
       "断网时证据保留在本地，网络恢复后按指数退避补传。"],
    ];
    disc.forEach(([t, d], i) => {
      const y = y0 + 2.46 + i * 0.87;
      T.card(s, M + 6.14, y, 5.95, 0.78);
      s.addText(t, { x: M + 6.38, y: y + 0.08, w: 5.5, h: 0.28, fontFace: F, fontSize: 11,
        bold: true, color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 6.38, y: y + 0.34, w: 5.50, h: 0.42, fontFace: F, fontSize: 9.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 12.5 });
    });
    T.foot(s, "证据包是本系统对外的唯一权威记录；告警快通路允许丢失，证据包保证不丢");
    s.addNotes("本页说明如何度量主动复核的效果。三个指标中像素密度比与 Δconf 达标，复核成功率未达标，原因已定位。右侧三条是实现上的约定，其中统计口径这一条值得展开：Δconf 必须按结论分组统计，误报组为负值，与真缺陷组混在一起算均值会接近零，看上去像复核没有作用。");
  }
};
