// 四、总结与创新点
const T = require("./theme");
const { C, F, W, H, M } = T;
const P = require("./newpages");

module.exports = function (pres, IMG) {

  T.divider(pres, "四", "总结与创新点", "本节回答：做了多少工作？完成到什么程度？有哪些创新？", ["研究工作量统计与质量保障措施", "研究进度：已完成九项、进行中两项、未开始两项", "三条识别路线的阶段性结论", "四条主要创新点及其实测支撑", "研究过程中形成的一条方法论"]);

  // ============================================================ 8 工作量总览
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究工作量统计", "四周内完成一套可运行、可测量、可现场演示的系统");
    const big = [["21 100", "行 Python 代码", C.amber], ["501", "项自动化测试", C.green],
                 ["4 700", "行技术文档", C.steel], ["22", "次版本提交", C.text]];
    big.forEach(([v, k, col], i) => {
      const x = M + i * 3.10;
      T.card(s, x, y0, 2.90, 1.24);
      T.stat(s, x + 0.28, y0 + 0.16, 2.4, v, k, col);
    });

    const mods = [
      ["patrol/perception", "3 665", "四路模型 + 融合仲裁 + 成像质量"],
      ["tests", "5 900", "501 项用例，覆盖率 75 %"],
      ["patrol/tools", "2 534", "校验 / 预览 / 标定 / 整定 / 假小车"],
      ["patrol/drivers", "2 277", "四个抽象基类 + 桩 + 真机串口"],
      ["training", "1 418", "数据集接入 / 三类训练 / ONNX·RKNN 导出"],
      ["patrol/mission", "1 309", "十状态机 / PID 伺服 / 抑制 / 预算"],
      ["patrol/scene", "1 131", "针孔光学 / 世界模型 / 表计绘制"],
      ["patrol/common", "869", "双时间戳 / 报文构造 / ZeroMQ 封装"],
      ["patrol/uploader", "918", "证据打包 / 断点续传 / 保留策略"],
      ["patrol/gateway", "697", "五项校验 / 参数硬限 / 心跳看门狗"],
      ["cloud", "500", "FastAPI + SQLite 台账 + 五个页签"],
    ];
    const rows = [[T.th("模块"), T.th("行数"), T.th("职责")]];
    mods.forEach(m => rows.push([
      T.td(m[0], { fontSize: 10.5, bold: true }),
      T.td(m[1], { fontSize: 10.5, align: "right", color: C.amber, bold: true }),
      T.td(m[2], { fontSize: 10.5 })]));
    T.table(s, rows, { x: M, y: y0 + 1.44, w: 7.55, colW: [2.15, 0.90, 4.50], rowH: 0.242 });

    const extra = [["5", "份冻结 JSON Schema", "四条接口的契约，全部 additionalProperties: false"],
                   ["12", "份技术文档", "架构、设计思想、ICD、代码地图、新手上路、演示指南…"],
                   ["4", "路识别模型", "L1 检测 / L2 分割 + OCR / L3 异常 / L4 显式仲裁"],
                   ["9", "个可当场演示的能力", "越界拒绝、看门狗接管、200 ms 中止、变焦对比…"]];
    extra.forEach(([v, t, d], i) => {
      const y = y0 + 1.44 + i * 0.94;
      T.card(s, M + 7.86, y, 4.23, 0.84);
      s.addText(v, { x: M + 8.06, y, w: 0.72, h: 0.84, align: "center", fontFace: F,
        fontSize: 21, bold: true, color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(t, { x: M + 8.80, y: y + 0.10, w: 3.20, h: 0.30, fontFace: F, fontSize: 11.5,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 8.80, y: y + 0.40, w: 3.20, h: 0.38, fontFace: F, fontSize: 9.3,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 12 });
    });
    T.foot(s, "统计口径：git 仓库实测，不含空行与第三方依赖");
    s.addNotes("本页讲工作量。重点不在总行数，而在构成：测试 5 900 行，与核心业务代码规模接近，说明测试与功能是同步写的，不是最后补的。文档 4 700 行，保证其他人接手时有依据可循。");
  }

  // ============================================================ 9 质量保障
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "质量保障措施与执行结果", "三项检查，每次提交前全部执行");
    const gates = [
      ["接口一致性校验", "51 项", "Schema 与代码、网关硬编码常量与 Schema 范围逐条交叉比对。" +
        "其中 9 条为反例，构造越界报文，必须全部被拦截。", C.amber, "python -m patrol.tools.validate"],
      ["自动化测试", "501 项", "覆盖率 75 %。含端到端 300 s 全链路、串口协议往返、状态机超时、" +
        "网关五项校验、云端台账、证据保留策略。", C.green, "python -m pytest -q"],
      ["端到端实跑", "300 s", "起全系统跑一轮真实巡检，产出证据包并统计复核增益。" +
        "没有产出数据即视为未跑通，不接受仅在设计上成立的结论。", C.steel, "python -m patrol.tools.run_all --seconds 300"],
    ];
    gates.forEach(([t, n, d, col, cmd], i) => {
      const x = M + i * 4.13;
      T.card(s, x, y0, 3.87, 3.30);
      s.addText(n, { x: x + 0.28, y: y0 + 0.20, w: 3.3, h: 0.62, fontFace: F, fontSize: 30,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(t, { x: x + 0.28, y: y0 + 0.86, w: 3.3, h: 0.34, fontFace: F, fontSize: 14,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: x + 0.28, y: y0 + 1.26, w: 3.32, h: 1.44, fontFace: F, fontSize: 11,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
      T.card(s, x + 0.20, y0 + 2.76, 3.47, 0.40, C.ink);
      s.addText(cmd, { x: x + 0.32, y: y0 + 2.76, w: 3.25, h: 0.40, fontFace: "Courier New",
        fontSize: 8.5, color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
    });

    T.card(s, M, y0 + 3.54, W - M * 2, 1.52, C.amberSoft);
    s.addText("一条贯穿全项目的记录原则：没有测到的写「未测」，测出来不理想的照实写", {
      x: M + 0.34, y: y0 + 3.70, w: 11.4, h: 0.36, fontFace: F, fontSize: 14.5, bold: true,
      color: "8A5A05", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("例如重复性 0.321 % FS 超出 0.3 % 的限值，我们把它写在 README 首页，而不是通过调整参数让它落进限值。" +
      "后一种做法只是掩盖了误差来源。指标偏低通常可以解释，指标来源不清则无法解释。", {
      x: M + 0.34, y: y0 + 4.10, w: 11.4, h: 0.82, fontFace: F, fontSize: 11.5,
      color: "8A5A05", isTextBox: true, margin: 0, valign: "top", lineSpacing: 16 });
    T.foot(s, "三项检查在每次提交前全部执行；validate 不通过就不提交");
    s.addNotes("本页讲质量保障。三项检查各是一条命令，可以当场演示。最后一段是我们一直遵守的记录原则：重复性超差主动写在 README 首页，不做修饰。");
  }

  // ==================================================== 完成度总表
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究进度：已完成、进行中、未开始",
      "逐项列出，包含尚未达标的指标与尚未开始的工作");
    const rows = [[T.th("模块"), T.th("状态"), T.th("依据"), T.th("尚未完成的部分")]];
    const data = [
      ["四进程闭环 + 云端台账", "已完成", "端到端 300 s 可重复运行，产出 4 至 6 个证据包", "无"],
      ["接口一致性", "已完成", "validate 51 项全部通过，含 9 条反例", "PTZ_RATE 为增补指令，尚未写入冻结 Schema"],
      ["自动化测试", "已完成", "501 项通过，整体覆盖率 75 %", "yolo.py 与 onnx_seg.py 仍为接口回归，无真权重用例"],
      ["安全边界三层", "已完成", "三条演示可现场执行", "无"],
      ["云台 PID 与增益调度", "已完成", "1× 超调 3.0 %，3× 超调 1.0 %，均达标", "故障率与复核成功率的关系曲线未做"],
      ["L2 几何读数", "已完成", "基本误差 0.469 % FS、线性度 0.267 % FS 达标", "重复性 0.321 % FS 超出 0.3 % 限值"],
      ["L4 融合仲裁", "已完成", "六种结论各有用例，每条附 reasons", "无"],
      ["L2′ OCR 互证", "已完成", "RapidOCR 离线权重，90 px 以上可读", "低像素密度档的误判率未系统评测"],
      ["L3 未知异常", "已完成", "PaDiM 全协方差，漏报 3.3 %、误报 3.8 %", "RKNN 上板与 INT8 掉点未测"],
      ["L2 学习分割", "进行中", "U-Net 指针 IoU 0.778，已接入读数链路", "核心比选图采样量不足，真实表盘误差表未做"],
      ["L1 目标检测", "进行中", "巡航级 mAP50 0.9949", "复核级未训；耗时、漏检率、链路切换对比未做"],
      ["五点标定", "未开始", "calibrate.py 已实现，覆盖率 0 %", "无真实标定数据，标定表尚未进配置"],
      ["真机联调", "未开始", "驱动层与串口协议已由假小车验证", "硬件未到位，属外部约束"],
    ];
    const col = { "已完成": C.green, "进行中": C.amber, "未开始": C.muted };
    const fillc = { "已完成": C.greenSoft, "进行中": C.amberSoft, "未开始": C.card };
    data.forEach(r => rows.push([
      T.td(r[0], { bold: true, fontSize: 9.5 }),
      T.td(r[1], { align: "center", bold: true, fontSize: 9.5, color: col[r[1]], fill: { color: fillc[r[1]] } }),
      T.td(r[2], { fontSize: 9.5 }),
      T.td(r[3], { fontSize: 9.5, color: r[3] === "无" ? C.green : C.muted })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [2.72, 1.05, 4.32, 3.99], rowH: 0.312 });

    const sum = [["9", "项已完成", C.green], ["2", "项进行中", C.amber], ["2", "项未开始", C.muted]];
    sum.forEach(([v, k, c], i) => {
      const x = M + i * 2.30;
      s.addText(v, { x, y: y0 + 4.42, w: 0.68, h: 0.50, fontFace: F, fontSize: 26, bold: true,
        color: c, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(k, { x: x + 0.72, y: y0 + 4.42, w: 1.50, h: 0.50, fontFace: F, fontSize: 11,
        color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
    });
    s.addText("两项未开始的都受外部条件限制：五点标定缺真实标定数据，真机联调缺硬件。" +
      "两项进行中的预计明日补齐。", {
      x: M + 7.00, y: y0 + 4.42, w: 5.09, h: 0.50, fontFace: F, fontSize: 10.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 14 });
    T.foot(s, "状态判定标准：能产出可复现的实测数据才记为已完成，否则记为进行中或未开始");
    s.addNotes("本页是完成度的逐项说明。状态判定采用统一标准：能产出可复现的实测数据才记为已完成。九项已完成，两项进行中，两项未开始。两项未开始的都受外部条件限制：五点标定缺真实标定数据，真机联调缺硬件。");
  }

  // ============================================================ 20 三路小结
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "三条识别路线的阶段性结论", "每条路线都给出了实测数据，也写明了尚未测试的部分");
    const rows = [[T.th("路线"), T.th("负责"), T.th("已完成 · 实测数"), T.th("比选结论"), T.th("待补")]];
    [["L1 目标检测", "甲", "巡航级 yolo11s：mAP50 0.9949 / mAP50-95 0.7513", "使用公开数据集训练，合成数据仅用于增广", "复核级、耗时、切换对比"],
     ["L2 语义分割", "乙", "针 IoU 0.251 → 0.384 → 0.778；耗时 59 ms 在预算内", "在合成表盘上与几何法不可区分，仍以几何法为默认实现", "核心图重画、真实图误差表"],
     ["L3 未知异常", "丙", "漏报 90.8 % → 3.3 %，误报 3.8 %；ROC 贴左上角", "PaDiM 全协方差版本最优，EfficientAD 因分数倒挂未采用", "上板 INT8 掉点"],
    ].forEach(r => rows.push([
      T.td(r[0], { bold: true, color: C.steel }), T.td(r[1], { align: "center", bold: true }),
      T.td(r[2]), T.td(r[3]), T.td(r[4], { color: C.amber })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [1.72, 0.62, 4.10, 3.35, 2.30], rowH: 0.86 });

    T.card(s, M, y0 + 3.70, W - M * 2, 1.50, C.card);
    s.addText("三条路线共同印证了一条设计判断", { x: M + 0.34, y: y0 + 3.86, w: 11.4, h: 0.34,
      fontFace: F, fontSize: 14, bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("L1 依靠公开数据集获得纹理多样性，L2 依靠真实标注学习指针与刻度的区分，L3 依靠合成正常样本避开缺陷数据不可得的约束。三条路线所需的数据类型完全不同，这是四类模型分开训练而不是合并为单一模型的实证依据。", {
      x: M + 0.34, y: y0 + 4.24, w: 11.4, h: 0.88, fontFace: F, fontSize: 11.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 16 });
    T.foot(s, "三份完整交付见 deliverables/ 下各自目录（一页纸 + 图 + 产物 + 复现命令）");
    s.addNotes("本页收束识别部分。最后一段是结论：三条路线所需的数据类型完全不同，这是分开做的实证依据。");
  }

  P.novelty(pres, IMG);

  // ============================================================ 24 方法论（深色）
  {
    const s = T.slide(pres);
    T.darkBg(s);
    s.addText("本阶段最重要的一条方法论", { x: M, y: 0.72, w: 11.5, h: 0.4, fontFace: F,
      fontSize: 14, bold: true, color: C.amber, charSpacing: 2, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("测试集太容易时，\n比选结论是被数据决定的，不是被方法决定的", {
      x: M, y: 1.24, w: 11.5, h: 1.50, fontFace: F, fontSize: 32, bold: true,
      color: C.white, isTextBox: true, margin: 0, valign: "top", lineSpacing: 46 });

    const three = [
      ["L1 检测", "mAP50 = 0.9949，epoch 1 就有 0.9759", "指标接近上限，复核级「mAP50 优于巡航级 5 个点」的验收标准已没有余量"],
      ["L2 分割", "n=24 时中位数噪声 0.053–0.228 % FS", "噪声区间覆盖了几何法与 U-Net 的全部差异，两者无法区分"],
      ["L3 异常", "统计法基线在原样本上误报漏报皆为 0", "改用难度更高的增广样本后才暴露出 90.8 % 的漏报，比选结果才具备区分度"],
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

    s.addText("三条路线各自独立遇到了同一个问题。其中只有丙主动提高了评测样本的难度，比选表才具备区分度。" +
      "这条经验已写入三份交付文档，作为下一阶段所有对比实验的前置检查项。", {
      x: M, y: 5.90, w: 11.5, h: 0.78, fontFace: F, fontSize: 12.5,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 18 });
    s.addText(String(T.pageNow()), { x: W - M - 0.6, y: H - 0.52, w: 0.6, h: 0.3, align: "right", fontFace: F,
      fontSize: 10, color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "middle" });
    s.addNotes("本页是三条路线的共性发现：评测集难度不足时，比选结论实际由数据决定而非由方法决定。三个人各自独立遇到了这个问题，因此把它作为下一阶段对比实验的前置检查项。");
  }

};
