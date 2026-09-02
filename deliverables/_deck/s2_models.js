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
    const y0 = T.head(s, "08", "识别层：为什么不是训一个更大的 YOLO", "「这块表有没有问题」看起来是一个问题，实际是四个，且四者对算力、精度与输出形式的要求互相冲突");
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
    T.foot(s, "详见 docs/多模型协同.md（五种协作关系：级联 / 接力 / 互证 / 兜底 / 仲裁）", 10);
    s.addNotes("本页是识别部分的总述。评审可能会问为什么不用一个端到端的大模型，答案在最后一段：四个子问题对速度、精度、输出形式和损失函数的要求互相冲突。而且这个划分是 ICD 协议中已经预留的，我们没有改动接口。");
  }

  // ============================================================ 11 L4 仲裁
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "09", "L4 显式仲裁：四路证据 → 六种结论", "全部为显式规则，每条结论附带 reasons 字段，判定依据可以逐级追溯");
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
    T.foot(s, "实现：patrol/perception/fusion.py（278 行，纯规则无学习成分）· 六种结论各有测试用例", 11);
    s.addNotes("仲裁层采用显式规则而非学习模型，是因为它必须能逐条给出判定依据。三条经验中第一条值得展开：举证责任的默认方向属于运维策略问题，不是算法问题。");
  }

  // ============================================================ 12 甲 L1 成果
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "10", "甲 · L1 目标检测：巡航级模型已训成", "训练集 distribution_room（公开数据集，2 773 张，CC BY 4.0），骨干 yolo11s，训练 120 轮");
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
    T.foot(s, "产物：deliverables/甲-检测/（stage_meta / args / results.csv / 7 张曲线图）", 12);
    s.addNotes("甲的巡航级模型已训练完成，指标较高。有一点要主动说明：第 1 轮 mAP50 就达到 0.976，这个数偏高。我们已经实现了 --check-leak 命令，用于排查 Roboflow 导出的增广副本跨训练集与验证集分布的情况，明天出结果。主动说明比被问到再解释更好。");
  }

  // ============================================================ 13 甲 L1 待补（槽位）
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "11", "甲 · L1 检测：待补齐部分", "复核级模型与链路切换对比，明天上午补入");
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
      "     指标变化要区分是检测精度下降，还是距离由真值改为估计值",
    ], "→ 明天上午由甲提供，替换本页");

    T.card(s, M, y0 + 4.14, W - M * 2, 1.06, C.ink);
    s.addText("切换对比比 mAP 更重要，因为它验证的是「先写代码、后训模型」这条路径是否成立：" +
      "上层代码不做任何修改，只改一项配置就能把训练好的权重接入全链路。", {
      x: M + 0.36, y: y0 + 4.14, w: 11.4, h: 1.06, fontFace: F, fontSize: 12,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 17 });
    T.foot(s, "本页为占位，明天上午补齐后重新生成", 13);
    s.addNotes("本页明天上午替换。如果答辩前甲仍未补齐，就按现在的内容讲，说明哪些项目尚未测试即可，不要临时填一个数字。");
  }

  // ============================================================ 14 乙 L2 IoU
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "12", "乙 · L2 语义分割：接上真实数据后 IoU 三级跃升", "指针类别的分割 IoU。公开数据集中只有 PaddleX 这一份提供了像素级指针标注");
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
    T.foot(s, "乙 顺带修了两个只有真跑过才会暴露的 bug：PaddleX 直链 404（且原指向检测集）、标注目录结构不匹配", 14);
    s.addNotes("乙这一路值得讲的不是 0.778，而是 0.251 到 0.384 这一步：模型不变，仅增加真实标注就提高 53 %，说明瓶颈在数据而不在模型容量。他修的两个缺陷也值得提一句，那是只有真正下载数据并完整跑通流程才会暴露的问题。");
  }

  // ============================================================ 15 乙 L2 数据接通证据
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "13", "乙 · L2：真实数据是否接入正确，只能通过人工检查叠加图确认", "掩膜错位不会反映在指标上。IoU 仍然正常，但模型学到的是错误的类别对应关系");
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
    T.foot(s, "验收标准原文：人眼查看 check/ 目录下的叠加图，确认掩膜没有错位。类别映射错一位，后续所有 IoU 都是错的，且不会报错", 15);
    s.addNotes("本页讲的是核对方法：有些错误不会反映在指标上，只能人工检查。类别映射错一位，IoU 仍然可以训得很高，但模型学到的对应关系是错的。因此我们把人工检查叠加图写成了硬性验收标准。");
  }

  // ============================================================ 16 乙 L2 比选（槽位）
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "14", "乙 · L2：几何法 vs 学习法的比选结论", "核心图正在重画，现有版本的采样量不足以支撑结论");
    T.card(s, M, y0, 5.9, 1.86);
    T.cardTitle(s, M + 0.26, y0 + 0.14, 5.4, "已确定的部分", C.green);
    s.addText([
      "级联单次耗时约 59 ms，占 VERIFY 预算 2 500 ms 的 2.4 %，在预算内",
      "分割替换的只是「哪些像素是针」，亚度级精度仍由几何解算给出",
      "两者读数误差同量级（0.06 至 0.19 % FS），均优于 0.5 % FS 的限值",
    ].map((t, i, a) => ({ text: t, options: { bullet: true, breakLine: i !== a.length - 1 } })), {
      x: M + 0.26, y: y0 + 0.50, w: 5.42, h: 1.00, fontFace: F, fontSize: 11,
      color: C.text, isTextBox: true, margin: 0, paraSpaceAfter: 5, lineSpacing: 15 });

    slot(s, M + 6.14, y0, 5.95, 1.86, "核心图重画（明早补）", [
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
    T.foot(s, "结论措辞取「在合成表盘上三者不可区分」，而非「几何法更优」。最终比选需要真实表盘的误差表", 16);
    s.addNotes("本页讲的是自查过程。我们没有直接采用第一版图，而是先估计了采样噪声，结果发现待比较的差异小于噪声。如果评审问怎么确认这个差异是真实的，这一页就是回答。");
  }

  // ============================================================ 17 丙 L3 比选表
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "15", "丙 · L3 未知异常检测：四方案横向比选", "非监督方法，训练只使用正常样本，避开缺陷标注数据不可得的约束");
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
    T.foot(s, "EfficientAD 简化蒸馏实测出现分数倒挂，异常样本得分反而更低，未采用。原因与数据一并记录在案", 17);
    s.addNotes("丙这一路完成度最高。四个方案在同一批样本、同一阈值下比较，结论明确。要强调 EfficientAD 这一列：该方案失败了，我们照实写进表里而不是删除，这是完整的比选记录。");
  }

  // ============================================================ 18 丙 L3 图
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "16", "丙 · L3：分数分布与 ROC", "正常与异常两组分数是否分离，可以直接从分布图判断");
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
    T.foot(s, "产物：deliverables/丙-异常/（l3_report.json / baseline.json / onnx_smoke.json / rknn_export.md）", 18);
    s.addNotes("左图最直观：两组分布分离即为可用，重叠即为不可用，不需要了解算法细节也能判断。右图 ROC 说明结论不依赖阈值的选取。");
  }

  // ============================================================ 19 丙 两个发现
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "17", "丙 · L3：两个决定结果的实测发现", "这两条解释了第一版为什么失败，比最终指标更有参考价值");
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
    T.foot(s, "两条都已写入交付文档。第一版为什么失败，比最终版指标多高更能说明流程是真正跑通的", 19);
    s.addNotes("第一条值得展开：离线数据集的分布与运行时管道实际送入的数据分布不一致，导致离线指标正常而接入系统后全部误报。这类问题只有把模型真正接进系统运行才会暴露，只在数据集上训练和评测是发现不了的。");
  }

  // ============================================================ 20 三路小结
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "18", "三条识别路线：阶段性小结", "每条路线都给出了实测数据，也写明了尚未测试的部分");
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
    T.foot(s, "三份完整交付见 deliverables/ 下各自目录（一页纸 + 图 + 产物 + 复现命令）", 20);
    s.addNotes("本页收束识别部分。最后一段是结论：三条路线所需的数据类型完全不同，这是分开做的实证依据。");
  }
};
