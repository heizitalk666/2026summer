// 第 10–20 页：识别层设计、L4 仲裁、甲 L1、乙 L2、丙 L3
const T = require("./theme");
const { C, F, W, H, M } = T;

/** 待补齐槽位（琥珀虚线框） */
function slot(s, x, y, w, h, title, items, when) {
  s.addShape("roundRect", {
    x, y, w, h, rectRadius: 0.06,
    fill: { color: "FDF6E8" },
    line: { color: C.amber, width: 1.75, dashType: "dash" },
  });
  s.addShape("ellipse", { x: x + 0.30, y: y + 0.26, w: 0.40, h: 0.40,
    fill: { color: C.amber }, line: { color: C.amber, width: 1 } });
  s.addText("!", { x: x + 0.30, y: y + 0.26, w: 0.40, h: 0.40, align: "center", valign: "middle",
    fontFace: F, fontSize: 15, bold: true, color: C.white, isTextBox: true, margin: 0 });
  s.addText(title, { x: x + 0.84, y: y + 0.24, w: w - 1.1, h: 0.44, fontFace: F, fontSize: 16,
    bold: true, color: "8A5A05", isTextBox: true, margin: 0, valign: "middle" });
  s.addText(items.map((t, i) => ({ text: t,
    options: { bullet: true, breakLine: i !== items.length - 1 } })), {
    x: x + 0.84, y: y + 0.76, w: w - 1.2, h: h - 1.28,
    fontFace: F, fontSize: 11.5, color: "6E4804", isTextBox: true, margin: 0,
    paraSpaceAfter: 5, lineSpacing: 17 });
  s.addText(when, { x: x + 0.84, y: y + h - 0.50, w: w - 1.2, h: 0.32, fontFace: F,
    fontSize: 10.5, bold: true, color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
}

module.exports = function (pres, IMG) {
  // ============================================================ 10 四类模型
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "08", "识别层：为什么不是训一个更大的 YOLO", "「这块表有没有问题」听上去是一个问题，做起来是四个，而且它们要的东西互相打架");
    const rows = [[T.th("问题"), T.th("要回答的"), T.th("模型类别"), T.th("为什么不能合进一个模型")]];
    [["在哪", "画面里有哪些设备，框在哪", "目标检测 L1", "要快、要全图扫。巡航期 30 Hz 只跑得起它"],
     ["多少", "指针指向几、液位到哪", "语义分割 + 几何 L2", "要像素级精度。只在放大后的小 ROI 上跑得起"],
     ["写的什么", "铭牌量程、单位、位置指示牌", "OCR L2′", "输出是字符串不是数，损失函数与评价指标都不同"],
     ["没见过的", "训练集里根本没有的异常", "非监督异常 L3", "有监督模型对未知类别系统性沉默——不是识别错，是压根不吭声"],
     ["信谁", "四路证据打架时怎么办", "显式规则仲裁 L4", "必须能逐条讲清「凭什么」，评审第一个问题就是这个"],
    ].forEach(r => rows.push([
      T.td(r[0], { bold: true, color: C.amber, align: "center" }),
      T.td(r[1]), T.td(r[2], { bold: true, color: C.steel }), T.td(r[3], { color: C.muted })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [1.15, 3.05, 2.10, 5.79], rowH: 0.53 });

    T.card(s, M, y0 + 3.42, W - M * 2, 1.76, C.ink);
    s.addText("一个模型全干的代价不是「精度差一点」，是每一路都被别的路拖到跑不动或学不会", {
      x: M + 0.36, y: y0 + 3.62, w: 11.4, h: 0.36, fontFace: F, fontSize: 14.5, bold: true,
      color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("要 30 Hz 就不能像素级，要像素级就不能全图扫，要认字就得换一套输出头，" +
      "要认没见过的就不能用有监督的损失函数。\n" +
      "这个拆法不是新加的概念——ICD 的 trigger_rule 枚举里 L1/L2/L3 三级判据阶梯本来就写在协议里，" +
      "verdict.result 的六个取值也正是一个仲裁层该有的六种结论。我们只是把协议里画好的格子填满，没有改任何 Schema。", {
      x: M + 0.36, y: y0 + 4.04, w: 11.4, h: 1.02, fontFace: F, fontSize: 11.5,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 16 });
    T.foot(s, "详见 docs/多模型协同.md（五种协作关系：级联 / 接力 / 互证 / 兜底 / 仲裁）", 10);
    s.addNotes("这一页是识别部分的总纲。评审很可能问「为什么不用一个端到端大模型」——答案在最后那段：四个问题对速度、精度、输出形式、损失函数的要求互相冲突。而且这个拆法是 ICD 协议里本来就留好的位置，我们没有改接口。");
  }

  // ============================================================ 11 L4 仲裁
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "09", "L4 显式仲裁：四路证据 → 六种结论", "纯规则、每条结论带 reasons——评审能顺着理由链问到底");
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
      ["举证责任的方向决定系统好不好用", "「证据不足」默认判正常还是判可疑，是个产品决策不是技术决策。" +
        "全判可疑会把人淹死，全判正常会漏掉真问题——所以要分情况显式写死。"],
      ["「证据不足」和「冲突」是两件事", "四路都没说话 ≠ 四路说了但互相矛盾。前者要补观测，后者要交人复核。" +
        "混成一类，运维就不知道该干什么。"],
      ["密度不达标就不下读数类结论", "fusion.py 的门槛是 120 × 0.8 = 96 px。观测条件不够时宁可报 INCONCLUSIVE，" +
        "也不出一个看起来精确的假读数。"],
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
    T.foot(s, "实现：patrol/perception/fusion.py（278 行，纯规则无学习成分）· 六种结论各有测试用例", 11);
    s.addNotes("仲裁层是纯规则不是学习的，这是有意的——它必须能逐条讲清凭什么。三条教训里第一条最值得讲：举证责任的方向是产品决策，不是技术决策。");
  }

  // ============================================================ 12 甲 L1 成果
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "10", "甲 · L1 目标检测：巡航级模型已训成", "公开数据集 distribution_room（2 773 张，CC BY 4.0）· yolo11s · 120 轮");
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
    s.addText("epoch 1 就有 mAP50 0.9759\n→ 需先排除 train/val 增广副本串台", {
      x: M + 9.18, y: y0 + 3.94, w: 2.62, h: 0.78, fontFace: F, fontSize: 10,
      bold: true, color: "8A5A05", isTextBox: true, margin: 0, valign: "middle", lineSpacing: 14 });
    T.foot(s, "产物：deliverables/甲-检测/（stage_meta / args / results.csv / 7 张曲线图）", 12);
    s.addNotes("甲的巡航级模型已经训完，指标很高。但要主动说明一件事：epoch 1 就有 0.976，这个数偏高，我们已经写了 --check-leak 命令去排查 Roboflow 增广副本跨 train/val 的问题，明天出结果。主动说比被问出来好。");
  }

  // ============================================================ 13 甲 L1 待补（槽位）
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "11", "甲 · L1 检测：待补齐部分", "复核级模型与链路切换对比——明天上午补入");
    slot(s, M, y0, 6.0, 3.92, "训练与指标（明早补）", [
      "复核级 yolo11m 训练与 mAP50（验收：优于巡航级）",
      "单帧推理耗时（限值 ≤ 33 ms，巡航期 30 Hz 的硬指标）",
      "漏检率（限值 ≤ 2 %）",
      "增广对比表：只用公开集 vs 公开集 + 合成集",
      "--check-leak 排查结果（train/val 增广副本）",
    ], "→ 明天上午由甲提供，替换本页");

    slot(s, M + 6.24, y0, 5.85, 3.92, "链路切换验证（明早补）", [
      "configs/system.yaml → perception.detector: yolo",
      "run_all 各跑三轮，对比证据包数 / 密度比 / Δconf / 成功率",
      "验收标准：不退化于合成检测器即为成功",
      "注意：切到 yolo 后距离由 bbox 反算而非真值透传，",
      "     指标变化要区分「检测变差」与「距离改估计」",
    ], "→ 明天上午由甲提供，替换本页");

    T.card(s, M, y0 + 4.14, W - M * 2, 1.06, C.ink);
    s.addText("为什么切换对比比 mAP 更重要：它证明「代码先行、模型后训」这条路走通了——" +
      "上层一行不改，只换配置就能把真权重接进全链路。", {
      x: M + 0.36, y: y0 + 4.14, w: 11.4, h: 1.06, fontFace: F, fontSize: 12,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 17 });
    T.foot(s, "本页为占位，明天上午补齐后重新生成", 13);
    s.addNotes("这一页明天上午替换。如果答辩前甲还没补上，就照现在这样讲——诚实说明哪些还没测，比编一个数字好。");
  }

  // ============================================================ 14 乙 L2 IoU
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "12", "乙 · L2 语义分割：接上真实数据后 IoU 三级跃升", "针的分割 IoU——公开数据里只有 PaddleX 那一份给了像素级指针标注");
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
    s.addText("最有价值的结论：卡住分割的是数据，不是模型容量", {
      x: M + 0.34, y: y0 + 4.10, w: 5.6, h: 0.32, fontFace: F, fontSize: 13, bold: true,
      color: "1C6B47", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("同一个 numpy 逻辑回归、同一套评测口径，只是把 PaddleX 的真实像素标注加进训练集，" +
      "IoU 就从 0.251 涨到 0.384（+53 %）。这直接说明真实标注是瓶颈——也印证了「针 vs 刻度」" +
      "这一环正是合成数据最教不会的地方。", {
      x: M + 0.34, y: y0 + 4.44, w: 11.4, h: 0.66, fontFace: F, fontSize: 11,
      color: "1C6B47", isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
    T.foot(s, "乙 顺带修了两个只有真跑过才会暴露的 bug：PaddleX 直链 404（且原指向检测集）、标注目录结构不匹配", 14);
    s.addNotes("乙这一路最有价值的不是 0.778 这个数，是 0.251→0.384 这一步——同一个模型只加真实数据就涨 53%，说明瓶颈在数据不在模型。另外他修的那两个 bug 值得提一句：那是只有真正下载并跑通才会暴露的问题。");
  }

  // ============================================================ 15 乙 L2 数据接通证据
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "13", "乙 · L2：真实数据接对了没有，只能靠人眼看", "掩膜错位在数字上完全看不出来——IoU 照样好看，但学的是错的东西");
    s.addImage({ path: IMG + "/l2_mask.png", x: M + 0.55, y: y0, w: 11.0, h: 3.52 });

    T.card(s, M, y0 + 3.66, 5.9, 1.52);
    T.cardTitle(s, M + 0.26, y0 + 3.80, 5.4, "两边类别对不齐，这是接的时候唯一要动脑子的地方", C.steel);
    s.addText("本项目  background · face · needle · ticks（四类）\n" +
      "PaddleX  background · pointer · scale（三类，没有盘面）\n" +
      "→ PaddleX 的 background 大部分其实是盘面，分不开，映射成 255 忽略、不进损失", {
      x: M + 0.26, y: y0 + 4.14, w: 5.42, h: 0.96, fontFace: F, fontSize: 10.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });

    T.card(s, M + 6.14, y0 + 3.66, 5.95, 1.52, C.greenSoft);
    T.cardTitle(s, M + 6.40, y0 + 3.80, 5.4, "分工因此变得清楚", "1C6B47");
    s.addText("针与刻度的区分  →  从真实数据学（合成数据最教不会的一环）\n" +
      "盘面与背景的区分  →  从合成数据学（合成标得毫无争议）", {
      x: M + 6.40, y: y0 + 4.14, w: 5.44, h: 0.96, fontFace: F, fontSize: 10.5,
      color: "1C6B47", isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
    T.foot(s, "验收标准原文：「人眼看 check/ 里的叠加图确认掩膜没错位」——类别映射错一位，后面所有 IoU 都是错的且不报错", 15);
    s.addNotes("这一页讲的是方法论：有些东西数字上看不出来，只能人眼核对。类别映射错一位，IoU 照样能训得很好看，但模型学的是错的东西。所以我们把「人眼看叠加图」写成了硬性验收标准。");
  }

  // ============================================================ 16 乙 L2 比选（槽位）
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "14", "乙 · L2：几何法 vs 学习法的比选结论", "核心图正在重画——现有版本的采样量不足以支撑结论");
    T.card(s, M, y0, 5.9, 1.86);
    T.cardTitle(s, M + 0.26, y0 + 0.14, 5.4, "已确定的部分", C.green);
    s.addText([
      "级联单次耗时 ≈ 59 ms，占 VERIFY 预算 2 500 ms 的 2.4 %——预算内",
      "分割替换的只是「哪些像素是针」，亚度级精度仍由几何解算给出",
      "读数误差两者同量级（0.06–0.19 % FS），均远优于 0.5 % FS 限值",
    ].map((t, i, a) => ({ text: t, options: { bullet: true, breakLine: i !== a.length - 1 } })), {
      x: M + 0.26, y: y0 + 0.50, w: 5.42, h: 1.00, fontFace: F, fontSize: 11,
      color: C.text, isTextBox: true, margin: 0, paraSpaceAfter: 5, lineSpacing: 15 });

    slot(s, M + 6.14, y0, 5.95, 1.86, "核心图重画（明早补）", [
      "采样量 n=24 → 200 以上",
      "纵轴改画 P90（中位数在这里会掩盖尾部风险）",
    ], "→ 明天上午替换 reading_error.png");

    T.card(s, M, y0 + 2.10, W - M * 2, 3.10, C.ink);
    s.addText("为什么这张图要重画——这是本次自查最有价值的一个发现", {
      x: M + 0.36, y: y0 + 2.28, w: 11.4, h: 0.36, fontFace: F, fontSize: 15, bold: true,
      color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
    const facts = [
      ["采样噪声大于待比差异", "同一个方法、同一档像素密度，只换随机种子重跑 40 次，中位数在 0.053–0.228 % FS 之间跳。" +
        "这个区间完整吞掉了几何法（0.06–0.14）与 U-Net（0.07–0.19）的全部差异——两者在 n=24 下无法区分。"],
      ["中位数是错的统计量", "把采样量提到 200 后中位数几乎不动，动的是尾部：P90 随密度单调下降 0.415 → 0.178 % FS。" +
        "像素密度买到的是「不出大错」，不是「典型误差更小」——改画 P90 反而正好支持我们的立论。"],
      ["合成表盘对几何法天然有利", "合成表盘的指针是死黑线画在近白盘面上，几何法「针是最暗贯穿条」的假设按构造成立且处在最大对比度。" +
        "分割要修的失效在这个测试集上根本没出现——所以结论只能说「在合成表盘上无法区分」，不能外推到真实表计。"],
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
    T.foot(s, "结论措辞：「在合成表盘上三者不可区分」，而非「几何法更优」——比选待真实图误差表", 16);
    s.addNotes("这一页其实是全场最能体现我们方法论水平的一页。我们没有把一张好看的图直接放上去，而是自己算了采样噪声，发现差异全在噪声里。这种自查比结论本身更值钱。评审如果问「你们怎么知道这个差异是真的」，这就是答案。");
  }

  // ============================================================ 17 丙 L3 比选表
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "15", "丙 · L3 未知异常检测：四方案横向比选", "非监督——只用「看起来正常」的样本，绕开缺陷数据不可得的约束");
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
      "部署目标是 RK3576 的 NPU，26 ms 在复核态预算内——这个取舍成立。", {
      x: M + 6.40, y: y0 + 4.06, w: 5.44, h: 0.96, fontFace: F, fontSize: 10.5,
      color: "1C6B47", isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
    T.foot(s, "EfficientAD 简化蒸馏实测分数倒挂（异常反而更低），未采用——原因与数据一并记录在案", 17);
    s.addNotes("丙这一路是三条里做得最完整的。四个方案在同一批样本、同一个阈值下比，结论清楚。要强调 EfficientAD 那一列——它失败了，我们照实写进表里而不是删掉，这是诚实比选。");
  }

  // ============================================================ 18 丙 L3 图
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "16", "丙 · L3：分数分布与 ROC", "两堆分数分不分得开，一眼就看得出来");
    s.addImage({ path: IMG + "/l3_score_dist.png", x: M, y: y0, w: 5.95, h: 3.82 });
    s.addImage({ path: IMG + "/l3_roc.png", x: M + 6.14, y: y0, w: 5.95, h: 3.82 });
    s.addText("四种方法的正常 / 异常分数分布（竖线 = 阈值 0.55）。全协方差 PaDiM 两簇几乎完全分开；" +
      "对角版在增广后区分度下降；统计法只抬不越线；EfficientAD 倒挂。", {
      x: M, y: y0 + 3.94, w: 5.95, h: 0.86, fontFace: F, fontSize: 10.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
    s.addText("阈值 0 → 1 全程扫描。全协方差 PaDiM 贴左上角，整条曲线站得住——" +
      "说明它不是靠某一个阈值点凑出来的成绩。", {
      x: M + 6.14, y: y0 + 3.94, w: 5.95, h: 0.86, fontFace: F, fontSize: 10.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
    T.foot(s, "产物：deliverables/丙-异常/（l3_report.json / baseline.json / onnx_smoke.json / rknn_export.md）", 18);
    s.addNotes("左图是最好讲的一张——两簇分开就是好，重叠就是不好，不需要懂算法也看得懂。右图 ROC 证明结论不依赖阈值选择。");
  }

  // ============================================================ 19 丙 两个发现
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "17", "丙 · L3：两个决定结果的实测发现", "比数字本身更值钱——它们解释了为什么第一版会失败");
    const f = [
      ["训练裁片必须模拟运行时的检测框噪声", C.red,
       "系统喂给 L3 的是检测框，而检测框是抖的，会把表盘「切边」。实测切边 12 % 的正常表盘，异常分从 0.5 直接跳到 1.0——全部误报。",
       "增广集加入抖动框后，系统实测的复核 ROI（600 s 跑出来的 6 张）从「全部 1.0 误报」回到正常分 0.00–0.09。",
       "离线数据集的分布必须和运行时管道真正喂进来的分布一致，否则离线指标再好，上线就崩。"],
      ["L3 与 L2 的分工在数据上看得见", C.green,
       "开位的开关被 L2 判为 READING_ABNORMAL（状态异常），而 L3 对它的外观给正常分。",
       "外观异常（FOREIGN_OBJECT 异物）才是 L3 该管的——两层各司其职，没有互相顶替。",
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
    T.foot(s, "两条都写进了交付文档——「为什么第一版失败」比「最终版多好」更能说明我们真的跑通了", 19);
    s.addNotes("第一条是全项目最好的一个 debug 故事：离线数据集和运行时管道的分布不一致，导致离线好看上线全错。这种问题只有真正把模型接进系统跑才会发现，纯做数据集是碰不到的。");
  }

  // ============================================================ 20 三路小结
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "18", "三条识别路线：阶段性小结", "每一条都给出了可测的数，也都写清了还没测的部分");
    const rows = [[T.th("路线"), T.th("负责"), T.th("已完成 · 实测数"), T.th("比选结论"), T.th("待补")]];
    [["L1 目标检测", "甲", "巡航级 yolo11s：mAP50 0.9949 / mAP50-95 0.7513", "公开集训练可用，合成集仅做增广", "复核级、耗时、切换对比"],
     ["L2 语义分割", "乙", "针 IoU 0.251 → 0.384 → 0.778；耗时 59 ms 在预算内", "合成表盘上与几何法不可区分，保持几何法为默认", "核心图重画、真实图误差表"],
     ["L3 未知异常", "丙", "漏报 90.8 % → 3.3 %，误报 3.8 %；ROC 贴左上角", "PaDiM 全协方差胜出，EfficientAD 倒挂未采用", "上板 INT8 掉点"],
    ].forEach(r => rows.push([
      T.td(r[0], { bold: true, color: C.steel }), T.td(r[1], { align: "center", bold: true }),
      T.td(r[2]), T.td(r[3]), T.td(r[4], { color: C.amber })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [1.72, 0.62, 4.10, 3.35, 2.30], rowH: 0.86 });

    T.card(s, M, y0 + 3.70, W - M * 2, 1.50, C.card);
    s.addText("三条路线共同印证了一条设计判断", { x: M + 0.34, y: y0 + 3.86, w: 11.4, h: 0.34,
      fontFace: F, fontSize: 14, bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("L1 靠公开数据训纹理多样性、L2 靠真实标注学「针 vs 刻度」、L3 靠合成正常样本绕开缺陷数据不可得——" +
      "三条路各自需要的数据完全不同。这正是「四类模型分开做」而不是「训一个更大的模型」的实证依据。", {
      x: M + 0.34, y: y0 + 4.24, w: 11.4, h: 0.88, fontFace: F, fontSize: 11.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 16 });
    T.foot(s, "三份完整交付见 deliverables/ 下各自目录（一页纸 + 图 + 产物 + 复现命令）", 20);
    s.addNotes("这一页收口识别部分。最后那段是升华：三条路需要的数据完全不同，这就是分开做的实证依据，不是拍脑袋。");
  }
};
