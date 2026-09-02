// 第 21–27 页：安全边界、云台控制、端到端、方法论、风险、下一步、结束
const T = require("./theme");
const { C, F, W, H, M } = T;

module.exports = function (pres, IMG) {
  // ============================================================ 21 安全边界
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "19", "安全边界：三层防线，每一条都能当场演示", "方案书说安全设计的说服力不在于声明，在于能当场演示");
    const layers = [
      ["第一层 · 指令白名单与参数硬限", C.red,
       "五项校验逐条上报 PASS / FAIL / SKIP。参数范围硬编码在代码里，不放配置——配置能被改，边界不能。",
       "pytest tests/test_gateway.py -k out_of_range -v", "越界指令被拒，并留下逐项校验的审计记录"],
      ["第二层 · 心跳看门狗", C.amber,
       "AI 进程崩掉后，网关 1.5 s 内接管，下发 RESUME 让车自己走完路线。这正是四进程拆分的意义。",
       "python -m patrol.tools.run_all &  然后  pkill -f patrol.mission.node", "杀掉 AI 进程后，车自己走完路线"],
      ["第三层 · 安全事件抢占", C.steel,
       "任何时刻的安全事件都能打断正在进行的复核，200 ms 内中止并回到安全状态。",
       "pytest tests/test_fsm.py -k safety -v", "注入安全事件，正在进行的复核 200 ms 内中止"],
    ];
    layers.forEach(([t, col, d, cmd, res], i) => {
      const y = y0 + i * 1.54;
      T.card(s, M, y, W - M * 2, 1.40);
      s.addShape("rect", { x: M, y: y, w: 0.09, h: 1.40, fill: { color: col }, line: { color: col, width: 0 } });
      s.addText(t, { x: M + 0.32, y: y + 0.12, w: 5.4, h: 0.34, fontFace: F, fontSize: 14,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 0.32, y: y + 0.48, w: 5.5, h: 0.84, fontFace: F, fontSize: 10.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });
      T.card(s, M + 6.02, y + 0.14, 6.05, 0.42, C.ink);
      s.addText(cmd, { x: M + 6.16, y: y + 0.14, w: 5.8, h: 0.42, fontFace: "Courier New",
        fontSize: 8.5, color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
      s.addText("→ " + res, { x: M + 6.02, y: y + 0.66, w: 6.05, h: 0.62, fontFace: F,
        fontSize: 10.5, bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
    });

    T.card(s, M, y0 + 4.70, W - M * 2, 0.62, C.card);
    s.addText("所有指向底盘和云台的动作只有 gateway 一个出口——这是安全边界第一层的物理保证，不是约定", {
      x: M + 0.34, y: y0 + 4.70, w: 11.4, h: 0.62, fontFace: F, fontSize: 11.5, bold: true,
      color: C.text, isTextBox: true, margin: 0, valign: "middle" });
    T.foot(s, "网关 697 行：node.py 收发与审计 / checks.py 五项校验 / limits.py 参数硬限 / watchdog.py 看门狗", 21);
    s.addNotes("三条演示都可以当场跑，加起来不到一分钟。如果评审对安全设计有疑问，直接演示第二条——杀掉 AI 进程，车照样走完路线，这个最有冲击力。");
  }

  // ============================================================ 22 云台控制
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "20", "云台控制：变焦增益调度是必需的，不是优化", "方案书 §11.1 点名的交付物——阶跃响应曲线（本次实测）");
    s.addImage({ path: IMG + "/pid_step.png", x: M, y: y0, w: 7.30, h: 4.46 });

    const rows = [[T.th("工况"), T.th("超调"), T.th("调节时间"), T.th("结论")]];
    [["1× 广角", "3.0 %", "0.901 s", "达标"],
     ["3× 变焦（有调度）", "1.0 %", "1.202 s", "达标"],
     ["3× 变焦（关调度）", "37.7 %", "1.102 s", "不可接受"]].forEach((r, i) => rows.push([
      T.td(r[0], { fontSize: 10.5, bold: i === 2 }),
      T.td(r[1], { fontSize: 10.5, align: "center", bold: true, color: i === 2 ? C.red : C.green }),
      T.td(r[2], { fontSize: 10.5, align: "center" }),
      T.td(r[3], { fontSize: 10.5, align: "center", color: i === 2 ? C.red : C.green, bold: true })]));
    T.table(s, rows, { x: M + 7.52, y: y0, w: 4.57, colW: [1.72, 0.92, 1.08, 0.85], rowH: 0.42 });

    T.card(s, M + 7.52, y0 + 1.86, 4.57, 1.62, C.card);
    T.cardTitle(s, M + 7.76, y0 + 2.00, 4.1, "为什么必须做增益调度", C.steel);
    s.addText("同样一度的云台转角，在 3× 变焦下画面里移动的像素是 1× 的三倍。" +
      "不按倍率缩放控制量，等于把增益直接放大三倍——必然过冲。\n\n" +
      "ω = θ / (W · z) · u", {
      x: M + 7.76, y: y0 + 2.36, w: 4.10, h: 1.06, fontFace: F, fontSize: 10.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });

    T.card(s, M + 7.52, y0 + 3.62, 4.57, 1.10, C.amberSoft);
    s.addText("超调 1.0 % → 37.7 %", { x: M + 7.76, y: y0 + 3.74, w: 4.1, h: 0.34, fontFace: F,
      fontSize: 15, bold: true, color: "8A5A05", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("关掉调度后过冲近 40 %，图中绿线一眼可见——这条对比就是「控」这一半的核心证据。", {
      x: M + 7.76, y: y0 + 4.10, w: 4.12, h: 0.56, fontFace: F, fontSize: 10,
      color: "8A5A05", isTextBox: true, margin: 0, valign: "top", lineSpacing: 13 });
    T.foot(s, "复现：python -m patrol.tools.tune_pid --out out/pid --compare-gain-schedule（本页数据为本次实测，README 旧值已更新）", 22);
    s.addNotes("课题名叫「测控系统」，「控」那一半的证据全在这一页。绿线那个大过冲最直观——关掉增益调度就是这个样子。这三组数是这次现场跑出来的，不是抄旧文档。");
  }

  // ============================================================ 23 端到端
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "21", "端到端：全系统跑一轮真实巡检", "四个进程 + 云端台账全链路，300 秒一轮，跑完出小结");
    const st = [["4 + 1", "进程全链路打通", C.green], ["4 – 6", "每轮产出证据包", C.steel],
                ["1.9 – 2.3", "复核前后像素密度比", C.green], ["+0.44", "真缺陷组 Δconf（目标 > +0.25）", C.green]];
    st.forEach(([v, k, col], i) => {
      const x = M + i * 3.10;
      T.card(s, x, y0, 2.90, 1.44);
      T.stat(s, x + 0.26, y0 + 0.26, 2.45, v, k, col);
    });

    T.card(s, M, y0 + 1.66, 7.30, 2.56, C.ink);
    s.addText("一轮巡检小结（实测输出）", { x: M + 0.30, y: y0 + 1.82, w: 6.7, h: 0.32, fontFace: F,
      fontSize: 12.5, bold: true, color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
    s.addText(
      "证据包 6 个，复核成功率 83.3 %（目标 > 85 %）\n\n" +
      "结论                条数    平均Δconf   平均密度比\n" +
      "READING_ABNORMAL       2      0.4441      2.2922\n" +
      "READING_OK             4      0.5644      1.5699\n\n" +
      "真缺陷组 Δconf 均值 = 0.4441（目标 > +0.25）", {
      x: M + 0.30, y: y0 + 2.20, w: 6.7, h: 1.94, fontFace: "Courier New", fontSize: 9.5,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 13 });

    T.card(s, M + 7.52, y0 + 1.66, 4.57, 2.56, C.amberSoft);
    T.cardTitle(s, M + 7.76, y0 + 1.82, 4.1, "如实报告：成功率未达目标", "8A5A05");
    s.addText("两轮实测为 83.3 % 与 75.0 %，均低于 85 % 的目标；文档记录的正常波动区间是 50 %–100 %。\n\n" +
      "主因是偶发的证据包配对失败——before 记录为空导致密度比为 0，该包不计入成功。" +
      "这是已定位、未修复的缺陷，写进了待办而不是调参数掩盖。", {
      x: M + 7.76, y: y0 + 2.20, w: 4.12, h: 1.94, fontFace: F, fontSize: 10.5,
      color: "8A5A05", isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });

    const chain = ["感知 30 Hz 巡航", "IF-1 可疑事件", "任务状态机决策", "IF-2 网关校验",
                   "执行器动作", "四路模型复核", "证据包组装", "云端台账入库"];
    chain.forEach((t, i) => {
      const x = M + i * 1.515;
      T.card(s, x, y0 + 4.42, 1.44, 0.72, i % 2 ? C.card : C.steelSoft);
      s.addText(t, { x: x + 0.06, y: y0 + 4.42, w: 1.32, h: 0.72, align: "center", valign: "middle",
        fontFace: F, fontSize: 9, color: C.text, isTextBox: true, margin: 0, lineSpacing: 12 });
    });
    T.foot(s, "复现：python -m patrol.tools.run_all --seconds 300　然后浏览器开 127.0.0.1:8000 看台账与实时页", 23);
    s.addNotes("这一页要主动讲成功率没达标。83.3% 和 75%，都低于 85% 目标。原因已经定位——证据包配对偶发丢 before 记录。我们把它写进待办而不是调参数凑过去。评审会欣赏这个态度，而且比被问出来好得多。");
  }

  // ============================================================ 24 方法论（深色）
  {
    const s = pres.addSlide();
    T.darkBg(s);
    s.addText("本阶段最重要的一条方法论", { x: M, y: 0.72, w: 11.5, h: 0.4, fontFace: F,
      fontSize: 14, bold: true, color: C.amber, charSpacing: 2, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("测试集太容易时，\n比选结论是被数据决定的，不是被方法决定的", {
      x: M, y: 1.24, w: 11.5, h: 1.50, fontFace: F, fontSize: 32, bold: true,
      color: C.white, isTextBox: true, margin: 0, valign: "top", lineSpacing: 46 });

    const three = [
      ["L1 检测", "mAP50 = 0.9949，epoch 1 就有 0.9759", "指标几乎打满，复核级「优于巡航级 5 个点」的验收标准已无余量"],
      ["L2 分割", "n=24 时中位数噪声 0.053–0.228 % FS", "噪声区间吞掉了几何法与 U-Net 的全部差异，两者无法区分"],
      ["L3 异常", "统计法基线在原样本上误报漏报皆为 0", "换成更难的增广样本后才暴露出 90.8 % 漏报，比选这才有意义"],
    ];
    three.forEach(([t, n, d], i) => {
      const x = M + i * 4.05;
      s.addShape("roundRect", { x, y: 3.20, w: 3.78, h: 2.42, rectRadius: 0.07,
        fill: { color: C.inkSoft }, line: { color: "33404F", width: 0.75 } });
      s.addText(t, { x: x + 0.26, y: 3.38, w: 3.3, h: 0.34, fontFace: F, fontSize: 14,
        bold: true, color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(n, { x: x + 0.26, y: 3.76, w: 3.3, h: 0.52, fontFace: F, fontSize: 11,
        bold: true, color: C.white, isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
      s.addText(d, { x: x + 0.26, y: 4.32, w: 3.30, h: 1.10, fontFace: F, fontSize: 10.5,
        color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });
    });

    s.addText("三条路线各自独立撞上同一件事——只有丙主动把样本做难，比选表才真正有信息量。" +
      "这条经验已经写进三份交付文档，作为下一阶段所有对比实验的前置检查。", {
      x: M, y: 5.90, w: 11.5, h: 0.78, fontFace: F, fontSize: 12.5,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 18 });
    s.addText("24", { x: W - M - 0.6, y: H - 0.52, w: 0.6, h: 0.3, align: "right", fontFace: F,
      fontSize: 10, color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "middle" });
    s.addNotes("这一页是全场最能体现方法论水平的地方。三个人各自独立撞上同一个坑：测试集太容易，导致比选结论其实是数据定的。这种跨路线的共性发现，比任何单个指标都更能说明我们真的在做研究而不是调参。");
  }

  // ============================================================ 25 风险
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "22", "风险与未决事项", "如实列出——包含两项已定位但尚未修复的缺陷");
    const rows = [[T.th("项目"), T.th("状态"), T.th("影响"), T.th("处置")]];
    const data = [
      ["重复性 0.321 % FS", "超差", "限值 0.3 %，压线且逐次浮动", "受检测框噪声支配，非算法极限；不调参掩盖，列入待办"],
      ["证据包配对偶发丢 before", "缺陷", "该包密度比为 0，复核成功率被拉低", "已定位到 uploader 配对逻辑，下一阶段修复"],
      ["复核成功率 75–83 %", "未达标", "目标 > 85 %", "主因同上；修复配对后重新评估"],
      ["mAP50 0.9949 偏高", "待核", "可能存在 train/val 增广副本串台", "已实现 --check-leak 排查命令，明日出结果"],
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
    s.addText("这一页刻意不做美化。项目从头到尾的原则是「证据不足时说不足，不猜」——" +
      "重复性超差挂在 README 首页，端到端成功率如实报 75–83 %，EfficientAD 失败照样写进比选表。" +
      "评审最容易问倒人的不是「你这个指标为什么低」，而是「你这个数是怎么来的」。", {
      x: M + 0.34, y: y0 + 3.88, w: 11.4, h: 1.28, fontFace: F, fontSize: 11.5,
      color: C.text, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 17 });
    T.foot(s, "六项风险中四项有明确处置路径，两项（真实读数真值、上板）受外部条件约束", 25);
    s.addNotes("这一页要坦然讲。六项风险里我们主动列了两个已定位未修复的缺陷。这种坦白反而会让评审觉得数据可信——因为一个全是好消息的报告本身就可疑。");
  }

  // ============================================================ 26 下一步
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "23", "下一阶段计划与分工", "按接口边界切分，四条线可并行推进");
    const plan = [
      ["甲 · L1 检测", C.steel, ["跑 --check-leak 排除 train/val 串台", "训练复核级 yolo11m 并导出 ONNX",
        "测单帧耗时与漏检率", "切 detector: yolo，run_all 三轮对比"]],
      ["乙 · L2 分割", C.steel, ["核心图重画：n≥200，纵轴改 P90", "补 split.json 交代训练/验证划分",
        "重跑 make_figures 修正图例配色", "寻找带读数真值的真实表盘数据"]],
      ["丙 · L3 异常", C.green, ["RKNN 导出与上板实测（等板子）", "记录 FP32 / INT8 两组数与掉点",
        "量化校准集从 evidence/ 取本场景图", "已完成主体，转入部署验证"]],
      ["组长 · 系统与交付", C.amber, ["修复证据包配对丢 before 的缺陷", "重复性超差归因（检测框噪声定量）",
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
    T.foot(s, "分工按接口边界切，不按文件数切——四个人可以并行改，不会互相踩", 26);
    s.addNotes("下一阶段四条线并行。丙那条已经基本完成，转入部署验证；组长这条主要是修缺陷和补可靠性曲线。");
  }

  // ============================================================ 27 结束
  {
    const s = pres.addSlide();
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
    s.addNotes("结束页。如果还有时间，主动提出演示——五条里挑一条，看门狗接管那条最有冲击力。");
  }
};
