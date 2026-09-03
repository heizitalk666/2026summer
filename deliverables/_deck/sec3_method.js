// 三、方法和研究内容
const T = require("./theme");
const { C, F, W, H, M } = T;
const P = require("./newpages");

module.exports = function (pres, IMG) {

  T.divider(pres, "三", "方法和研究内容", "本节回答：采用什么方法？从何开展？如何实施？资料从哪获取？", ["两项支撑性方法：虚拟试验台与接口冻结", "资料来源：两份公开数据集、合成数据与系统自产证据包", "九项研究内容：识别层四路模型、安全边界、云台伺服、端到端闭环", "每一项都给出实施方式、实测数据与复现命令", "两处标注为待补的实测项，将于明日补入并重新出图"]);

  // ============================================================ 7 无硬件并行开发
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究方法（一）：虚拟试验台与故障注入", "虚拟配电室按针孔投影渲染，因此精度与控制指标是实测值而非推导值");
    s.addImage({ path: IMG + "/thirdperson_compare.png", x: M, y: y0, w: 7.55, h: 2.48 });
    s.addText("第三人称机位同时画出车身、云台朝向与当前视锥。变焦 1× 到 3× 时视场角由 60.0° 收窄到 21.8°，" +
      "视锥落在被复核的表盘上。指令下发、云台转动、视场覆盖目标这一串过程因此可以直接观察，不必只看日志。", {
      x: M, y: y0 + 2.58, w: 7.55, h: 0.78, fontFace: F, fontSize: 11,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 16 });

    const pts = [
      ["桩不是空实现", "桩会注入真机上实际存在的故障：ACK 丢包 2 %、对焦失败 5 %、云台角速度上限、安全事件 0.05 次/分。如果驱动只是把目标值直接赋给状态量，任何到位判据都会通过，验证就没有意义。"],
      ["真值与先验严格分开", "world.py 中的 truth 只提供给渲染器和评分逻辑，感知侧读不到。否则精度指标等于用真值去核对真值。这条约束是评测结果可信的前提。"],
      ["硬件到位只改两处", "configs/system.yaml 的 driver_mode: stub → real，configs/real.yaml 填端口。上位机代码一行不动。"],
    ];
    pts.forEach(([t, d], i) => {
      const y = y0 + i * 1.42;
      T.card(s, M + 7.86, y, 4.23, 1.30);
      T.cardTitle(s, M + 8.10, y + 0.13, 3.8, t, C.amber);
      s.addText(d, { x: M + 8.10, y: y + 0.44, w: 3.80, h: 0.78, fontFace: F, fontSize: 9.8,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 13 });
    });

    T.card(s, M, y0 + 3.52, 7.55, 1.34, C.greenSoft);
    s.addText("真车到之前，串口链路已经用「假小车」验通", {
      x: M + 0.28, y: y0 + 3.66, w: 7.0, h: 0.32, fontFace: F, fontSize: 13, bold: true,
      color: "1C6B47", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("fakecar 以独立进程运行，字节经过内核（POSIX 用 PTY，Windows 用 TCP 环回）。" +
      "分帧、CRC 校验、超时、重传与 2 % ACK 丢包注入均按原样发生，可以验证协议栈与时序逻辑的正确性。", {
      x: M + 0.28, y: y0 + 4.02, w: 7.0, h: 0.72, fontFace: F, fontSize: 10.5,
      color: "1C6B47", isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });
    T.foot(s, "虚拟配电室 1 131 行 · 驱动抽象层 2 277 行（四个 ABC + 桩 + 真机串口/V4L2）");
    s.addNotes("本页回答没有硬件如何开展工作。有两点：一是桩会注入真机上实际存在的故障，因此在桩上通过的验收是有意义的；二是真值与先验严格分开，感知读不到真值，精度数据不是自我核对得出的。假小车这一条可以证明真机驱动代码路径处于可运行状态。");
  }

  // ==================================================== 接口冻结机制
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究方法（二）：接口冻结与一致性校验",
      "四个人并行开发的前提，是报文结构在开发过程中保持稳定");
    const rows = [[T.th("Schema"), T.th("承载接口"), T.th("关键约束")]];
    [["detection_event", "IF-1 感知到任务/上传", "trigger_rule 枚举含 CONF_BAND / L2_UNREADABLE / L3_ANOMALY 三级判据"],
     ["control_command", "IF-2 任务到网关", "command 枚举 6 条；每条指令的参数范围与网关硬编码常量逐条比对"],
     ["command_ack", "IF-2 网关回执", "ACCEPTED 时不得携带 reject_code；拒绝时必须给出失败的校验项"],
     ["status_report", "IF-3 网关广播", "含云台位姿、底盘状态、安全事件；20 Hz 周期发送并支持插播"],
     ["evidence_package", "IF-4 上传到云端", "verdict.result 六个取值；gain 三项增益指标；files[].role 枚举"],
    ].forEach(r => rows.push([
      T.td(r[0], { bold: true, fontSize: 10.5, color: C.steel }),
      T.td(r[1], { fontSize: 10.5 }), T.td(r[2], { fontSize: 10.5, color: C.muted })]));
    T.table(s, rows, { x: M, y: y0, w: 7.55, colW: [1.82, 2.05, 3.68], rowH: 0.50 });

    T.card(s, M + 7.86, y0, 4.23, 3.00);
    T.cardTitle(s, M + 8.10, y0 + 0.16, 3.8, "改动成本表", C.amber);
    const cost = [["新增可选字段、新增枚举值", "次版本号 +1，通知即可", C.green],
                  ["修改字段语义、类型、范围", "主版本号 +1，全组重评审，三个桩同步改", C.amber],
                  ["增删指令白名单", "需重新评审安全边界，默认不批准", C.red]];
    cost.forEach(([a, b, col], i) => {
      const y = y0 + 0.58 + i * 0.80;
      s.addText(a, { x: M + 8.10, y, w: 3.80, h: 0.30, fontFace: F, fontSize: 11,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(b, { x: M + 8.10, y: y + 0.30, w: 3.80, h: 0.44, fontFace: F, fontSize: 10,
        color: col, isTextBox: true, margin: 0, valign: "top", lineSpacing: 13 });
    });

    const guard = [
      ["51 项一致性校验", "Schema 与代码、网关硬编码常量与 Schema 范围逐条交叉比对，每次提交前执行"],
      ["9 条反例", "构造越界报文与非法字段组合，必须全部被 Schema 拦截，防止校验形同虚设"],
      ["ALLOWED_DRIFT 白名单", "确需偏离冻结基线时，必须登记在白名单中并说明理由，不允许未经登记直接增删字段"],
    ];
    guard.forEach(([t, d], i) => {
      const y = y0 + 3.22 + i * 0.72;
      T.card(s, M, y, W - M * 2, 0.64);
      s.addText(t, { x: M + 0.28, y, w: 2.70, h: 0.64, fontFace: F, fontSize: 11.5,
        bold: true, color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 3.06, y, w: 8.80, h: 0.64, fontFace: F, fontSize: 10.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
    });
    T.foot(s, "五份 Schema 全部 additionalProperties: false；ICD 冻结于 M1（D3）评审");
    s.addNotes("这一页讲接口治理。重点是改动成本表：不同类型的改动对应不同的评审要求，增删指令白名单默认不批准。加上 51 项校验和 9 条反例，接口在开发过程中就能保持稳定。");
  }

  P.data(pres, IMG);

  // ============================================================ 10 四类模型
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（一）：多模型协同的识别层设计", "「这块表有没有问题」看起来是一个问题，实际是四个，且四者对算力、精度与输出形式的要求互相冲突");
    const rows = [[T.th("问题"), T.th("要回答的"), T.th("模型类别"), T.th("为什么不能合进一个模型")]];
    [["在哪", "画面里有哪些设备，框在哪", "目标检测 L1", "需要全图扫描且延迟低。巡航期 30 Hz 只能负担这一路"],
     ["多少", "指针指向几、液位到哪", "语义分割 + 几何 L2", "需要像素级精度，只能在放大后的小 ROI 上运行"],
     ["写的什么", "铭牌量程、单位、位置指示牌", "OCR L2′", "输出是字符串不是数，损失函数与评价指标都不同"],
     ["没见过的", "训练集里根本没有的异常", "非监督异常 L3", "有监督模型对训练集之外的类别不会输出，属于系统性漏检而非误判"],
     ["信谁", "四路证据互相矛盾时如何取舍", "显式规则仲裁 L4", "必须能逐条给出判定依据，这是评审首先会问的"],
    ].forEach(r => rows.push([
      T.td(r[0], { bold: true, color: C.amber, align: "center" }),
      T.td(r[1]), T.td(r[2], { bold: true, color: C.steel }), T.td(r[3], { color: C.muted })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [1.15, 3.05, 2.10, 5.79], rowH: 0.53 });

    T.card(s, M, y0 + 3.42, W - M * 2, 1.76, C.ink);
    s.addText("用单一模型承担全部任务，代价不是精度略有下降，而是每一路都会被其余几路的约束限制到无法运行或无法收敛", {
      x: M + 0.36, y: y0 + 3.62, w: 11.4, h: 0.36, fontFace: F, fontSize: 14.5, bold: true,
      color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("要保持 30 Hz 就无法做像素级分割；要做像素级分割就无法全图扫描；要识别字符就要换一套输出头；" +
      "要识别训练集之外的异常就不能用有监督的损失函数。\n" +
      "这个划分不是我们新加的：ICD 的 trigger_rule 枚举中已经写明 L1、L2、L3 三级判据，" +
      "verdict.result 的六个取值也对应仲裁层的六种结论。我们只是把协议里已经预留的位置填上，没有修改任何 Schema。", {
      x: M + 0.36, y: y0 + 4.04, w: 11.4, h: 1.02, fontFace: F, fontSize: 11.5,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 16 });
    T.foot(s, "详见 docs/多模型协同.md（五种协作关系：级联 / 接力 / 互证 / 兜底 / 仲裁）");
    s.addNotes("本页是识别部分的总述。评审可能会问为什么不用一个端到端的大模型，答案在最后一段：四个子问题对速度、精度、输出形式和损失函数的要求互相冲突。而且这个划分是 ICD 协议中已经预留的，我们没有改动接口。");
  }

  // ============================================================ 11 L4 仲裁
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（二）：L4 显式仲裁的判定规则", "全部为显式规则，每条结论附带 reasons 字段，判定依据可以逐级追溯");
    const verds = [
      ["CONFIRMED_DEFECT", "确认缺陷", C.red], ["READING_ABNORMAL", "读数越界", C.red],
      ["READING_OK", "读数正常", C.green], ["FALSE_ALARM", "误报消解", C.steel],
      ["UNKNOWN_ANOMALY", "未知异常", C.amber], ["INCONCLUSIVE", "证据不足", C.muted]];
    verds.forEach(([n, cn, col], i) => {
      const x = M + (i % 3) * 4.13, y = y0 + Math.floor(i / 3) * 1.06;
      T.card(s, x, y, 3.87, 0.94);
      s.addShape("ellipse", { x: x + 0.24, y: y + 0.24, w: 0.34, h: 0.34,
        fill: { color: col }, line: { color: col, width: 1 } });
      s.addText(n, { x: x + 0.70, y: y + 0.10, w: 3.0, h: 0.34, fontFace: F, fontSize: 12,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(cn, { x: x + 0.70, y: y + 0.44, w: 3.0, h: 0.28, fontFace: F, fontSize: 10.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
    });

    const lessons = [
      ["举证责任的方向决定系统好不好用", "「证据不足」默认判为正常还是可疑，属于运维策略问题，不是算法问题。" +
        "全部判为可疑会产生大量无效工单，全部判为正常会漏掉真实缺陷，因此按触发条件分情况显式规定。"],
      ["「证据不足」和「冲突」是两件事", "四路都没有输出，与四路都有输出但结论矛盾，是两种情况。前者应补充观测，后者应转人工复核。" +
        "合并为一类，运维就无法判断下一步该做什么。"],
      ["密度不达标就不下读数类结论", "fusion.py 的门槛是 120 × 0.8 = 96 px。观测条件不满足时输出 INCONCLUSIVE，" +
        "而不给出一个数值精确但实际不可信的读数。"],
    ];
    lessons.forEach(([t, d], i) => {
      const y = y0 + 2.22 + i * 1.12;
      T.card(s, M, y, W - M * 2, 1.02);
      s.addShape("ellipse", { x: M + 0.26, y: y + 0.30, w: 0.40, h: 0.40,
        fill: { color: C.amberSoft }, line: { color: C.amber, width: 1.25 } });
      s.addText(String(i + 1), { x: M + 0.26, y: y + 0.30, w: 0.40, h: 0.40, align: "center",
        valign: "middle", fontFace: F, fontSize: 12, bold: true, color: C.amber, isTextBox: true, margin: 0 });
      T.cardTitle(s, M + 0.84, y + 0.14, 10.8, t);
      s.addText(d, { x: M + 0.84, y: y + 0.46, w: 10.9, h: 0.50, fontFace: F, fontSize: 11,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
    });
    T.foot(s, "实现：patrol/perception/fusion.py（278 行，纯规则无学习成分）· 六种结论各有测试用例");
    s.addNotes("仲裁层采用显式规则而非学习模型，是因为它必须能逐条给出判定依据。三条经验中第一条值得展开：举证责任的默认方向属于运维策略问题，不是算法问题。");
  }

  // ============================================================ 12 甲 L1 成果
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（三）：L1 目标检测的训练与结果", "训练集 distribution_room（公开数据集，2 773 张，CC BY 4.0），骨干 yolo11s，训练 120 轮");
    const st = [["0.9949", "mAP50", C.green], ["0.7513", "mAP50-95", C.steel],
                ["0.9967", "precision", C.steel], ["0.9976", "recall", C.steel]];
    st.forEach(([v, k, col], i) => {
      const x = M + i * 3.10;
      T.card(s, x, y0, 2.90, 1.38);
      T.stat(s, x + 0.28, y0 + 0.22, 2.4, v, k, col);
    });
    s.addImage({ path: IMG + "/l1_pr.png", x: M, y: y0 + 1.58, w: 3.85, h: 3.28 });
    s.addImage({ path: IMG + "/l1_results.png", x: M + 4.06, y: y0 + 1.58, w: 4.60, h: 3.28 });

    T.card(s, M + 8.86, y0 + 1.58, 3.23, 3.28);
    T.cardTitle(s, M + 9.08, y0 + 1.76, 2.8, "训练配置", C.steel);
    s.addText([
      "骨干  yolo11s（预训练起训）",
      "轮次  120 epochs",
      "输入  640 × 640，batch 4",
      "划分  沿用 Roboflow 原始 train/valid",
      "映射  原始类别 → 本项目三类状态量",
    ].map((t, i, a) => ({ text: t, options: { bullet: true, breakLine: i !== a.length - 1 } })), {
      x: M + 9.08, y: y0 + 2.14, w: 2.82, h: 1.56, fontFace: F, fontSize: 10.5,
      color: C.text, isTextBox: true, margin: 0, paraSpaceAfter: 5, lineSpacing: 15 });
    T.card(s, M + 9.02, y0 + 3.86, 2.92, 0.94, C.amberSoft);
    s.addText("第 1 轮 mAP50 已达 0.9759\n需先排除训练集与验证集之间\n存在同源增广副本", {
      x: M + 9.18, y: y0 + 3.94, w: 2.62, h: 0.78, fontFace: F, fontSize: 10,
      bold: true, color: "8A5A05", isTextBox: true, margin: 0, valign: "middle", lineSpacing: 14 });
    T.foot(s, "产物：deliverables/甲-检测/（stage_meta / args / results.csv / 7 张曲线图）");
    s.addNotes("甲的巡航级模型已训练完成，指标较高。有一点要主动说明：第 1 轮 mAP50 就达到 0.976，这个数偏高。我们已经实现了 --check-leak 命令，用于排查 Roboflow 导出的增广副本跨训练集与验证集分布的情况，明天出结果。主动说明比被问到再解释更好。");
  }

  // ==================================================== 甲 L1 分类表现
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（三·续）：识别对象的确定与分类表现",
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

  // ============================================================ 13 甲 L1 待补（槽位）
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（三·续）：待补充的实测项", "复核级模型与链路切换对比，明天上午补入");
    T.slot(s, M, y0, 6.0, 3.92, "训练与指标（明早补）", [
      "复核级 yolo11m 训练与 mAP50（验收：优于巡航级）",
      "单帧推理耗时（限值 ≤ 33 ms，巡航期 30 Hz 的硬指标）",
      "漏检率（限值 ≤ 2 %）",
      "增广对比表：只用公开集 vs 公开集 + 合成集",
      "--check-leak 排查结果（train/val 增广副本）",
    ], "→ 明天上午由甲提供，替换本页");

    T.slot(s, M + 6.24, y0, 5.85, 3.92, "链路切换验证（明早补）", [
      "configs/system.yaml → perception.detector: yolo",
      "run_all 各跑三轮，对比证据包数 / 密度比 / Δconf / 成功率",
      "验收标准：不退化于合成检测器即为成功",
      "注意：切到 yolo 后距离由 bbox 反算而非真值透传，",
      "     指标变化要区分是检测精度下降，还是距离由真值改为估计值",
    ], "→ 明天上午由甲提供，替换本页");

    T.card(s, M, y0 + 4.14, W - M * 2, 1.06, C.ink);
    s.addText("切换对比比 mAP 更重要，因为它验证的是「先写代码、后训模型」这条路径是否成立：" +
      "上层代码不做任何修改，只改一项配置就能把训练好的权重接入全链路。", {
      x: M + 0.36, y: y0 + 4.14, w: 11.4, h: 1.06, fontFace: F, fontSize: 12,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 17 });
    T.foot(s, "本页为占位，明天上午补齐后重新生成");
    s.addNotes("本页明天上午替换。如果答辩前甲仍未补齐，就按现在的内容讲，说明哪些项目尚未测试即可，不要临时填一个数字。");
  }

  // ============================================================ 14 乙 L2 IoU
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（四）：L2 语义分割的三级对比", "指针类别的分割 IoU。公开数据集中只有 PaddleX 这一份提供了像素级指针标注");
    s.addImage({ path: IMG + "/l2_iou.png", x: M, y: y0, w: 6.10, h: 3.78 });

    const steps = [["0.251", "纯合成掩膜训练", "链路先跑通的基线", C.muted],
                   ["0.384", "合成 + PaddleX 真实标注", "同一模型，仅加真实数据 → +0.133", C.steel],
                   ["0.778", "U-Net（合成 + PaddleX）", "同一评测口径下再翻一倍", C.green]];
    steps.forEach(([v, t, d, col], i) => {
      const y = y0 + i * 1.31;
      T.card(s, M + 6.34, y, 5.75, 1.19);
      s.addText(v, { x: M + 6.56, y, w: 1.34, h: 1.02, fontFace: F, fontSize: 25, bold: true,
        color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(t, { x: M + 7.96, y: y + 0.14, w: 3.9, h: 0.34, fontFace: F, fontSize: 12.5,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 7.96, y: y + 0.50, w: 3.92, h: 0.40, fontFace: F, fontSize: 10.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });
    });

    T.card(s, M, y0 + 3.98, W - M * 2, 1.22, C.greenSoft);
    s.addText("本路线的主要结论：限制分割精度的是训练数据，不是模型容量", {
      x: M + 0.34, y: y0 + 4.10, w: 5.6, h: 0.32, fontFace: F, fontSize: 13, bold: true,
      color: "1C6B47", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("模型与评测口径都不变，仅把 PaddleX 的真实像素标注加入训练集，指针 IoU 就从 0.251 升到 0.384，提高 53 %。这说明真实标注是当前的瓶颈，也验证了指针与刻度的区分正是合成掩膜最难提供监督信号的一环。", {
      x: M + 0.34, y: y0 + 4.44, w: 11.4, h: 0.66, fontFace: F, fontSize: 11,
      color: "1C6B47", isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
    T.foot(s, "乙 顺带修了两个只有真跑过才会暴露的 bug：PaddleX 直链 404（且原指向检测集）、标注目录结构不匹配");
    s.addNotes("乙这一路值得讲的不是 0.778，而是 0.251 到 0.384 这一步：模型不变，仅增加真实标注就提高 53 %，说明瓶颈在数据而不在模型容量。他修的两个缺陷也值得提一句，那是只有真正下载数据并完整跑通流程才会暴露的问题。");
  }

  // ============================================================ 15 乙 L2 数据接通证据
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "乙 · L2：真实数据是否接入正确，只能通过人工检查叠加图确认", "掩膜错位不会反映在指标上。IoU 仍然正常，但模型学到的是错误的类别对应关系");
    s.addImage({ path: IMG + "/l2_mask.png", x: M + 0.55, y: y0, w: 11.0, h: 3.52 });

    T.card(s, M, y0 + 3.66, 5.9, 1.52);
    T.cardTitle(s, M + 0.26, y0 + 3.80, 5.4, "两边类别对不齐，这是接的时候唯一要动脑子的地方", C.steel);
    s.addText("本项目  background · face · needle · ticks（四类）\n" +
      "PaddleX  background · pointer · scale（三类，没有盘面）\n" +
      "PaddleX 的 background 中大部分实际是盘面，两者无法区分，因此映射为 255 并排除出损失函数", {
      x: M + 0.26, y: y0 + 4.14, w: 5.42, h: 0.96, fontFace: F, fontSize: 10.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });

    T.card(s, M + 6.14, y0 + 3.66, 5.95, 1.52, C.greenSoft);
    T.cardTitle(s, M + 6.40, y0 + 3.80, 5.4, "分工因此变得清楚", "1C6B47");
    s.addText("指针与刻度的区分：由真实标注提供监督（合成掩膜在这一维上最弱）\n" +
      "盘面与背景的区分：由合成掩膜提供监督（合成数据在这一维上标注明确）", {
      x: M + 6.40, y: y0 + 4.14, w: 5.44, h: 0.96, fontFace: F, fontSize: 10.5,
      color: "1C6B47", isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
    T.foot(s, "验收标准原文：人眼查看 check/ 目录下的叠加图，确认掩膜没有错位。类别映射错一位，后续所有 IoU 都是错的，且不会报错");
    s.addNotes("本页讲的是核对方法：有些错误不会反映在指标上，只能人工检查。类别映射错一位，IoU 仍然可以训得很高，但模型学到的对应关系是错的。因此我们把人工检查叠加图写成了硬性验收标准。");
  }

  // ============================================================ 16 乙 L2 比选（槽位）
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（四·续）：几何法与学习法的比选", "核心图正在重画，现有版本的采样量不足以支撑结论");
    T.card(s, M, y0, 5.9, 1.86);
    T.cardTitle(s, M + 0.26, y0 + 0.14, 5.4, "已确定的部分", C.green);
    s.addText([
      "级联单次耗时约 59 ms，占 VERIFY 预算 2 500 ms 的 2.4 %，在预算内",
      "分割替换的只是「哪些像素是针」，亚度级精度仍由几何解算给出",
      "两者读数误差同量级（0.06 至 0.19 % FS），均优于 0.5 % FS 的限值",
    ].map((t, i, a) => ({ text: t, options: { bullet: true, breakLine: i !== a.length - 1 } })), {
      x: M + 0.26, y: y0 + 0.50, w: 5.42, h: 1.00, fontFace: F, fontSize: 11,
      color: C.text, isTextBox: true, margin: 0, paraSpaceAfter: 5, lineSpacing: 15 });

    T.slot(s, M + 6.14, y0, 5.95, 1.86, "核心图重画（明早补）", [
      "采样量 n=24 → 200 以上",
      "纵轴改用 P90，中位数在这里会掩盖尾部误差",
    ], "→ 明天上午替换 reading_error.png");

    T.card(s, M, y0 + 2.10, W - M * 2, 3.10, C.ink);
    s.addText("这张图要重画的原因，也是本次自查的主要发现", {
      x: M + 0.36, y: y0 + 2.28, w: 11.4, h: 0.36, fontFace: F, fontSize: 15, bold: true,
      color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
    const facts = [
      ["采样噪声大于待比差异", "同一方法、同一档像素密度，仅更换随机种子重跑 40 次，中位数在 0.053 至 0.228 % FS 之间波动。" +
        "这个区间覆盖了几何法（0.06 至 0.14）与 U-Net（0.07 至 0.19）的全部差异，因此在 n=24 下两者无法区分。"],
      ["中位数是错的统计量", "采样量提高到 200 后中位数基本不变，变化的是尾部：P90 随像素密度单调下降，由 0.415 降到 0.178 % FS。" +
        "像素密度改善的是大误差的发生概率，不是典型误差，因此改用 P90 反而更支持本项目的论点。"],
      ["合成表盘对几何法天然有利", "合成表盘的指针是近黑色线段绘制在近白盘面上，几何法「指针是盘面上最暗的贯穿条」这一假设按构造成立，且处于最大对比度。分割方法要解决的失效模式在这个测试集上没有出现，因此结论只能限定为「在合成表盘上无法区分」，不能外推到真实表计。"],
    ];
    facts.forEach(([t, d], i) => {
      const y = y0 + 2.76 + i * 0.80;
      s.addText("0" + (i + 1), { x: M + 0.36, y, w: 0.44, h: 0.62, fontFace: F, fontSize: 13,
        bold: true, color: C.amber, isTextBox: true, margin: 0, valign: "top" });
      s.addText([{ text: t + "　", options: { bold: true, color: C.white } },
                 { text: d, options: { color: C.mutedOnInk } }], {
        x: M + 0.88, y, w: 10.9, h: 0.76, fontFace: F, fontSize: 10.5,
        isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });
    });
    T.foot(s, "结论措辞取「在合成表盘上三者不可区分」，而非「几何法更优」。最终比选需要真实表盘的误差表");
    s.addNotes("本页讲的是自查过程。我们没有直接采用第一版图，而是先估计了采样噪声，结果发现待比较的差异小于噪声。如果评审问怎么确认这个差异是真实的，这一页就是回答。");
  }

  // ============================================================ 17 丙 L3 比选表
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（五）：L3 未知异常的四方案比选", "非监督方法，训练只使用正常样本，避开缺陷标注数据不可得的约束");
    const rows = [[T.th("指标"), T.th("统计法\n基线·零权重"), T.th("EfficientAD\n简化蒸馏"),
                   T.th("PaDiM\n对角"), T.th("PaDiM 全协方差\n（采用）")]];
    const data = [
      ["误报率（正常裁片）", "1.9 %", "0.0 %", "3.8 %", "3.8 %"],
      ["漏报率（异常 120 张）", "90.8 %", "100 %（分数倒挂）", "48.3 %", "3.3 %"],
      ["正常均分 / 异常均分", "0.03 / 0.36", "0.07 / 0.00", "0.04 / 0.56", "0.06 / 0.95"],
      ["权重大小", "1.6 KB", "2.8 MB", "3.1 MB", "≈ 44 MB"],
      ["单次打分（CPU）", "6 ms", "未启用", "22 ms", "26 ms"],
      ["可解释", "✅ 说得清哪个通道", "❌", "❌", "❌"],
    ];
    data.forEach((r, ri) => rows.push([
      T.td(r[0], { bold: true, fontSize: 11 }),
      T.td(r[1], { align: "center", fontSize: 11 }),
      T.td(r[2], { align: "center", fontSize: 11, color: C.muted }),
      T.td(r[3], { align: "center", fontSize: 11 }),
      T.td(r[4], { align: "center", fontSize: 11, bold: true,
        color: ri === 1 ? C.green : C.text, fill: { color: C.greenSoft } })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [2.68, 2.20, 2.35, 1.95, 2.91], rowH: 0.445 });

    T.card(s, M, y0 + 3.48, 5.9, 1.62, C.card);
    T.cardTitle(s, M + 0.26, y0 + 3.62, 5.4, "评测条件（同一批样本，阈值统一 0.55）", C.steel);
    s.addText("训练集  796 张正常裁片（密度分层 259 + 复核几何/检测框抖动增广 537）\n" +
      "评测集  另种子正常裁片 106 张 + 异常裁片 120 张\n" +
      "         （场景 FOREIGN_OBJECT 60 + 训练与场景都没见过的合成贴片 60）", {
      x: M + 0.26, y: y0 + 3.98, w: 5.42, h: 1.02, fontFace: F, fontSize: 10.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });

    T.card(s, M + 6.14, y0 + 3.48, 5.95, 1.62, C.greenSoft);
    s.addText("漏报率 90.8 % → 3.3 %", { x: M + 6.40, y: y0 + 3.62, w: 5.4, h: 0.40,
      fontFace: F, fontSize: 17, bold: true, color: "1C6B47", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("代价是权重从 1.6 KB 涨到 44 MB、打分从 6 ms 到 26 ms，且失去可解释性。" +
      "部署目标是 RK3576 的 NPU，26 ms 在复核态预算内，因此这个取舍成立。", {
      x: M + 6.40, y: y0 + 4.06, w: 5.44, h: 0.96, fontFace: F, fontSize: 10.5,
      color: "1C6B47", isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
    T.foot(s, "EfficientAD 简化蒸馏实测出现分数倒挂，异常样本得分反而更低，未采用。原因与数据一并记录在案");
    s.addNotes("丙这一路完成度最高。四个方案在同一批样本、同一阈值下比较，结论明确。要强调 EfficientAD 这一列：该方案失败了，我们照实写进表里而不是删除，这是完整的比选记录。");
  }

  // ==================================================== 丙 L3 误报漏报对比
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（五·续）：误报与漏报的成因分析",
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

  // ============================================================ 18 丙 L3 图
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（五·续）：分数分布与 ROC 曲线", "正常与异常两组分数是否分离，可以直接从分布图判断");
    s.addImage({ path: IMG + "/l3_score_dist.png", x: M, y: y0, w: 5.95, h: 3.82 });
    s.addImage({ path: IMG + "/l3_roc.png", x: M + 6.14, y: y0, w: 5.95, h: 3.82 });
    s.addText("四种方法的正常 / 异常分数分布（竖线 = 阈值 0.55）。全协方差 PaDiM 两簇几乎完全分开；" +
      "对角版在增广后区分度下降；统计法只抬不越线；EfficientAD 倒挂。", {
      x: M, y: y0 + 3.94, w: 5.95, h: 0.86, fontFace: F, fontSize: 10.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
    s.addText("阈值由 0 扫描到 1。全协方差 PaDiM 的曲线靠近左上角，整条曲线均可用，" +
      "说明该结果不依赖于某一个特定阈值。", {
      x: M + 6.14, y: y0 + 3.94, w: 5.95, h: 0.86, fontFace: F, fontSize: 10.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
    T.foot(s, "产物：deliverables/丙-异常/（l3_report.json / baseline.json / onnx_smoke.json / rknn_export.md）");
    s.addNotes("左图最直观：两组分布分离即为可用，重叠即为不可用，不需要了解算法细节也能判断。右图 ROC 说明结论不依赖阈值的选取。");
  }

  // ============================================================ 19 丙 两个发现
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（五·续）：两项决定结果的实测发现", "这两条解释了第一版为什么失败，比最终指标更有参考价值");
    const f = [
      ["训练裁片必须模拟运行时的检测框噪声", C.red,
       "系统送入 L3 的是检测框，而检测框存在抖动，会把表盘边缘裁掉。实测裁掉 12 % 的正常表盘，异常分由 0.5 升到 1.0，全部误报。",
       "增广集加入抖动框后，系统实测的复核 ROI（600 s 跑出来的 6 张）从「全部 1.0 误报」回到正常分 0.00–0.09。",
       "离线数据集的分布必须与运行时管道实际送入的数据分布一致，否则离线指标再好，接入系统后也会失效。"],
      ["L3 与 L2 的分工在数据上看得见", C.green,
       "开位的开关被 L2 判为 READING_ABNORMAL（状态异常），而 L3 对它的外观给正常分。",
       "外观异常（FOREIGN_OBJECT 异物）由 L3 负责。两层分工明确，没有互相覆盖。",
       "这条印证了「四类模型分开做」的设计：它们回答的不是同一个问题，所以不该用同一个指标去衡量。"],
    ];
    f.forEach(([t, col, a, b, c], i) => {
      const y = y0 + i * 2.44;
      T.card(s, M, y, W - M * 2, 2.14);
      s.addShape("ellipse", { x: M + 0.28, y: y + 0.26, w: 0.46, h: 0.46,
        fill: { color: col }, line: { color: col, width: 1 } });
      s.addText(String(i + 1), { x: M + 0.28, y: y + 0.26, w: 0.46, h: 0.46, align: "center",
        valign: "middle", fontFace: F, fontSize: 14, bold: true, color: C.white, isTextBox: true, margin: 0 });
      s.addText(t, { x: M + 0.92, y: y + 0.22, w: 10.6, h: 0.42, fontFace: F, fontSize: 16,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText([{ text: "现象　", options: { bold: true, color: col } },
                 { text: a, options: { color: C.text } }], {
        x: M + 0.92, y: y + 0.70, w: 10.7, h: 0.42, fontFace: F, fontSize: 11,
        isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
      s.addText([{ text: "处置　", options: { bold: true, color: col } },
                 { text: b, options: { color: C.text } }], {
        x: M + 0.92, y: y + 1.12, w: 10.7, h: 0.42, fontFace: F, fontSize: 11,
        isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
      s.addText([{ text: "教训　", options: { bold: true, color: C.muted } },
                 { text: c, options: { color: C.muted } }], {
        x: M + 0.92, y: y + 1.60, w: 10.7, h: 0.62, fontFace: F, fontSize: 10.5,
        isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });
    });
    T.foot(s, "两条都已写入交付文档。第一版为什么失败，比最终版指标多高更能说明流程是真正跑通的");
    s.addNotes("第一条值得展开：离线数据集的分布与运行时管道实际送入的数据分布不一致，导致离线指标正常而接入系统后全部误报。这类问题只有把模型真正接进系统运行才会暴露，只在数据集上训练和评测是发现不了的。");
  }

  // ============================================================ 21 安全边界
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（六）：三层安全边界的设计与验证", "按方案书的要求，安全设计需要以可现场复现的方式验证，而不是仅在文档中声明");
    const layers = [
      ["第一层 · 指令白名单与参数硬限", C.red,
       "五项校验逐条上报 PASS、FAIL 或 SKIP。参数范围硬编码在源码中，不放在配置文件里：配置可以被修改，安全边界不应该。",
       "pytest tests/test_gateway.py -k out_of_range -v", "越界指令被拒，并留下逐项校验的审计记录"],
      ["第二层 · 心跳看门狗", C.amber,
       "感知与任务进程异常退出后，网关在 1.5 s 内接管，下发 RESUME 使车辆按原路线走完。这是四进程划分要保证的能力。",
       "python -m patrol.tools.run_all &  然后  pkill -f patrol.mission.node", "终止感知与任务进程后，车辆仍按路线走完"],
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
    s.addText("所有指向底盘与云台的动作只经过 gateway 一个出口。这是第一层安全边界在代码结构上的保证，不依赖各模块自觉遵守", {
      x: M + 0.34, y: y0 + 4.70, w: 11.4, h: 0.62, fontFace: F, fontSize: 11.5, bold: true,
      color: C.text, isTextBox: true, margin: 0, valign: "middle" });
    T.foot(s, "网关 697 行：node.py 收发与审计 / checks.py 五项校验 / limits.py 参数硬限 / watchdog.py 看门狗");
    s.addNotes("三条演示都可以当场执行，合计不到一分钟。如果评审对安全设计有疑问，建议直接演示第二条：终止感知与任务进程后车辆仍按路线走完。");
  }

  // ============================================================ 22 云台控制
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（七）：云台伺服与变焦增益调度", "方案书 §11.1 列出的交付物：云台阶跃响应曲线，本页为本次实测结果");
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
    s.addText("同样 1° 的云台转角，在 3× 变焦下对应的画面位移是 1× 的三倍。" +
      "若控制量不按变焦倍率缩放，等效于把回路增益放大三倍，必然产生过冲。\n\n" +
      "ω = θ / (W · z) · u", {
      x: M + 7.76, y: y0 + 2.36, w: 4.10, h: 1.06, fontFace: F, fontSize: 10.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });

    T.card(s, M + 7.52, y0 + 3.62, 4.57, 1.10, C.amberSoft);
    s.addText("超调 1.0 % → 37.7 %", { x: M + 7.76, y: y0 + 3.74, w: 4.1, h: 0.34, fontFace: F,
      fontSize: 15, bold: true, color: "8A5A05", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("关闭增益调度后过冲接近 40 %，对应图中绿色曲线。这组对比是本课题控制部分的主要实测依据。", {
      x: M + 7.76, y: y0 + 4.10, w: 4.12, h: 0.56, fontFace: F, fontSize: 10,
      color: "8A5A05", isTextBox: true, margin: 0, valign: "top", lineSpacing: 13 });
    T.foot(s, "复现：python -m patrol.tools.tune_pid --out out/pid --compare-gain-schedule（本页数据为本次实测，README 旧值已更新）");
    s.addNotes("课题名称是「测控系统」，控制部分的实测依据集中在这一页。绿色曲线的过冲最直观，那是关闭增益调度后的响应。三组数据均为本次现场运行所得，不是引用旧文档。");
  }

  // ============================================================ 23 端到端
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（八）：端到端闭环的实测验证", "四个边缘进程与云端台账构成全链路，单轮 300 秒，结束后输出统计小结");
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
      "主要原因是证据包偶发配对失败：before 记录为空，导致密度比为 0，该包不计入成功。" +
      "这是已定位但尚未修复的缺陷，已列入待办，没有通过调整判定条件规避。", {
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
    T.foot(s, "复现：python -m patrol.tools.run_all --seconds 300　然后浏览器开 127.0.0.1:8000 看台账与实时页");
    s.addNotes("本页要主动说明成功率未达标。83.3 % 与 75.0 % 均低于 85 % 的目标。原因已定位为证据包配对偶发丢失 before 记录，已列入待办，没有通过调整判定条件规避。主动说明比被问到再解释更好。");
  }

  // ==================================================== 证据包与复核增益
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容（九）：复核效果的度量方法",
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
