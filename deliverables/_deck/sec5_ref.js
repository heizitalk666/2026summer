// 五、参考文献与存在的不足
const T = require("./theme");
const { C, F, W, H, M } = T;
const P = require("./newpages");

module.exports = function (pres, IMG) {

  T.divider(pres, "五", "参考文献与存在的不足", "本节回答：参考了哪些资料？怎么处理的？还存在哪些不足？", ["规范文件、公开数据集、方法来源三类参考资料", "参考资料的处理原则：引用之后落到可执行的检查上", "六项存在的不足，含两项已定位但尚未修复的缺陷", "下一阶段的工作计划与分工", "两项尚未开始的工作及其外部制约条件"]);

  P.refs(pres, IMG);

  // ============================================================ 25 风险
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "存在的不足与待解决问题", "如实列出，其中包含两项已定位但尚未修复的缺陷");
    const rows = [[T.th("项目"), T.th("状态"), T.th("影响"), T.th("处置")]];
    const data = [
      ["重复性 0.321 % FS", "超差", "限值 0.3 %，压线且逐次浮动", "主要受检测框噪声影响，不是读数算法的精度上限。不通过调参掩盖，列入待办"],
      ["证据包配对偶发丢 before", "缺陷", "该包密度比为 0，复核成功率被拉低", "已定位到 uploader 配对逻辑，下一阶段修复"],
      ["复核成功率 75–83 %", "未达标", "目标 > 85 %", "主因同上；修复配对后重新评估"],
      ["mAP50 0.9949 偏高", "待核", "训练集与验证集可能存在同源增广副本", "已实现 --check-leak 排查命令，明日出结果"],
      ["真实表盘读数误差", "未测", "L2 比选无法外推到真实表计", "PaddleX 无读数真值，需另找带真值的数据或人工标注"],
      ["RKNN 上板实测", "阻塞", "INT8 掉点与实际帧率未知", "导出链路已通过 ONNX 冒烟推理，等板子"],
    ];
    const stCol = { "超差": C.red, "缺陷": C.red, "未达标": C.red, "待核": C.amber, "未测": C.amber, "阻塞": C.muted };
    data.forEach(r => rows.push([
      T.td(r[0], { bold: true, fontSize: 11 }),
      T.td(r[1], { align: "center", bold: true, color: stCol[r[1]], fontSize: 11 }),
      T.td(r[2], { fontSize: 11 }), T.td(r[3], { color: C.muted, fontSize: 11 })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [2.62, 1.10, 3.20, 5.17], rowH: 0.545 });

    T.card(s, M, y0 + 3.88, W - M * 2, 1.28, C.card);
    s.addText("本页不做修饰。项目一贯的记录原则是：证据不足时写明不足，不做推测。" +
      "重复性超差写在 README 首页，端到端成功率如实记为 75 % 至 83 %，EfficientAD 方案失败也照样写进比选表。" +
      "指标偏低通常可以解释，指标来源不清则无法解释。", {
      x: M + 0.34, y: y0 + 3.88, w: 11.4, h: 1.28, fontFace: F, fontSize: 11.5,
      color: C.text, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 17 });
    T.foot(s, "六项风险中四项有明确处置路径，两项（真实表盘读数真值、上板实测）受外部条件限制");
    s.addNotes("本页照实讲。六项风险中我们主动列出了两个已定位但尚未修复的缺陷。完整披露有助于说明其余数据的可信度。");
  }

  // ============================================================ 26 下一步
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "下一阶段工作计划与分工", "按接口边界划分，四条线可并行推进");
    const plan = [
      ["甲 · L1 检测", C.steel, ["运行 --check-leak，排除训练集与验证集的同源样本", "训练复核级 yolo11m 并导出 ONNX",
        "测单帧耗时与漏检率", "切 detector: yolo，run_all 三轮对比"]],
      ["乙 · L2 分割", C.steel, ["核心图重画：n≥200，纵轴改 P90", "补 split.json 交代训练/验证划分",
        "重新运行 make_figures，修正图例配色", "寻找带读数真值的真实表盘数据"]],
      ["丙 · L3 异常", C.green, ["RKNN 导出与上板实测（等板子）", "记录 FP32 / INT8 两组数与掉点",
        "量化校准集从 evidence/ 取本场景图", "主体已完成，转入部署验证"]],
      ["组长 · 系统与交付", C.amber, ["修复证据包配对丢失 before 记录的缺陷", "重复性超差归因（检测框噪声定量）",
        "故障率 → 复核成功率的可靠性曲线", "答辩材料与演示脚本收口"]],
    ];
    plan.forEach(([t, col, items], i) => {
      const x = M + (i % 2) * 6.14, y = y0 + Math.floor(i / 2) * 2.46;
      T.card(s, x, y, 5.95, 2.28);
      s.addShape("ellipse", { x: x + 0.26, y: y + 0.22, w: 0.40, h: 0.40,
        fill: { color: col }, line: { color: col, width: 1 } });
      s.addText(["甲", "乙", "丙", "长"][i], { x: x + 0.26, y: y + 0.22, w: 0.40, h: 0.40,
        align: "center", valign: "middle", fontFace: F, fontSize: 12, bold: true,
        color: C.white, isTextBox: true, margin: 0 });
      s.addText(t, { x: x + 0.80, y: y + 0.20, w: 4.9, h: 0.42, fontFace: F, fontSize: 15,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(items.map((tx, k) => ({ text: tx,
        options: { bullet: true, breakLine: k !== items.length - 1 } })), {
        x: x + 0.80, y: y + 0.72, w: 4.90, h: 1.46, fontFace: F, fontSize: 10.5,
        color: C.muted, isTextBox: true, margin: 0, paraSpaceAfter: 3, lineSpacing: 14 });
    });
    T.foot(s, "分工按接口边界划分，不按文件数量划分，四个人可以并行修改各自的模块而不产生冲突");
    s.addNotes("下一阶段四条线并行。丙这一路主体已完成，转入部署验证；组长这一路主要是修复缺陷和补充可靠性曲线。");
  }

  // ============================================================ 27 结束
  {
    const s = T.slide(pres);
    T.darkBg(s);
    s.addShape("ellipse", { x: 5.02, y: 1.62, w: 3.30, h: 3.30,
      fill: { color: C.ink }, line: { color: "2C3846", width: 1.25 } });
    s.addShape("ellipse", { x: 5.47, y: 2.07, w: 2.40, h: 2.40,
      fill: { color: C.ink }, line: { color: C.amber, width: 1.75 } });
    // 指针绕形状中心旋转，中心必须落在轴心上
    s.addShape("rect", { x: 6.6475, y: 2.72, w: 0.045, h: 1.10,
      fill: { color: C.amber }, line: { color: C.amber, width: 0.5 }, rotate: 35 });
    s.addShape("ellipse", { x: 6.52, y: 3.14, w: 0.30, h: 0.30,
      fill: { color: C.amber }, line: { color: C.amber, width: 1 } });

    s.addText("敬请指正", { x: 0, y: 5.30, w: W, h: 0.62, align: "center", fontFace: F,
      fontSize: 30, bold: true, color: C.white, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("基于 RK3576 边缘计算的无人车主动式 AI 巡检系统 · 中期答辩", {
      x: 0, y: 5.98, w: W, h: 0.34, align: "center", fontFace: F, fontSize: 12,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("现场可演示：变焦对比 · 越界指令被拒 · 看门狗接管 · 复核 200 ms 中止 · 端到端一轮巡检", {
      x: 0, y: 6.42, w: W, h: 0.32, align: "center", fontFace: F, fontSize: 10.5,
      color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
    s.addNotes("结束页。如果时间允许，主动提出现场演示，从五条中选一条，建议选看门狗接管那条。");
  }

};
