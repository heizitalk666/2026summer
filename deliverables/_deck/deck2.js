// 后半程：识别层、三路比选、比选汇总（含非技术因素）、安全、控制与闭环、创新点、计划、文献
const T = require("./theme");
const { C, F, W, H, M } = T;

module.exports = function (pres, IMG) {

  // ============================================================ 识别层
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.识别层", "3.8　为什么不用一个大模型：四类模型分工与显式仲裁",
      ["四路模型分工，结论交纯规则的 L4 仲裁",
      "四个子问题对算力、精度与输出形式的要求互相冲突；而判定依据必须逐条可追溯"]);
    const rows = [[T.th("子问题"), T.th("要回答"), T.th("模型类别"), T.th("为什么不能合并为一个模型")]];
    [["在哪", "画面里有哪些设备，框在哪", "目标检测 L1", "需全图扫描且延迟低，巡航期 30 Hz 只负担得起这一路"],
     ["多少", "指针指向几、液位到哪", "语义分割 + 几何解算 L2", "需像素级精度，只能在放大后的小 ROI 上运行"],
     ["写的什么", "铭牌量程、单位、位置指示牌", "OCR L2′", "输出是字符串而非数值，损失函数与评价指标都不同"],
     ["没见过的", "训练集之外的异常", "非监督异常 L3", "有监督模型对训练集外类别不输出，属系统性漏检而非误判"],
     ["信谁", "四路证据矛盾时如何取舍", "显式规则仲裁 L4", "结论要能逐条给出判定依据，模型输出的置信度做不到这一点"],
    ].forEach(r => rows.push([
      T.td(r[0], { bold: true, color: C.red, align: "center", fontSize: 10 }),
      T.td(r[1], { fontSize: 9.8 }), T.td(r[2], { bold: true, color: C.blue, fontSize: 9.8 }),
      T.td(r[3], { color: C.muted, fontSize: 9.8 })]));
    T.table(s, rows, { x: M, y: y0, w: 8.05, colW: [1.05, 2.55, 1.85, 2.60], rowH: 0.50 });

    T.card(s, M + 8.28, y0, 4.10, 3.00);
    T.cardTitle(s, M + 8.50, y0 + 0.12, 3.7, "L4 仲裁：六种结论");
    const V = [["CONFIRMED_DEFECT", "确认缺陷", C.red], ["READING_ABNORMAL", "读数越界", C.red],
               ["READING_OK", "读数正常", C.green], ["FALSE_ALARM", "误报消解", C.blue],
               ["UNKNOWN_ANOMALY", "未知异常", C.amber], ["INCONCLUSIVE", "证据不足", C.muted]];
    V.forEach(([n, cn, col], i) => {
      const y = y0 + 0.48 + i * 0.40;
      s.addShape("ellipse", { x: M + 8.52, y: y + 0.10, w: 0.16, h: 0.16,
        fill: { color: col }, line: { color: col, width: 0.5 } });
      s.addText(n, { x: M + 8.78, y, w: 2.35, h: 0.36, fontFace: F, fontSize: 9.5,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(cn, { x: M + 11.16, y, w: 1.10, h: 0.36, fontFace: F, fontSize: 9.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
    });

    const L = [["一个模型全干的代价", "要保持 30 Hz 就无法做像素级分割；要做像素级分割就无法全图扫描；要识别字符就要换输出头；要识别训练集外异常就不能用有监督损失。四者对算力、精度与输出形式的要求互相冲突。"],
               ["仲裁层为何用规则而非模型", "任务书把「伦理与社会责任（AI 误判、数据隐私与可解释性）」列为必须考虑的非技术因素。规则仲裁的每条结论附带 reasons 字段，可逐级追溯，模型给不出这种可解释性。"],
               ["观测条件不足时的处理", "像素密度不达标时输出 INCONCLUSIVE 并交人复核，而不给出一个数值精确但实际不可信的读数。"]];
    L.forEach(([t, d], i) => {
      const y = y0 + 3.36 + i * 0.62;
      T.card(s, M, y, W - M * 2, 0.56);
      s.addText(t, { x: M + 0.22, y, w: 3.05, h: 0.56, fontFace: F, fontSize: 10.5,
        bold: true, color: C.navy, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 3.42, y, w: 8.95, h: 0.56, fontFace: F, fontSize: 9,
        color: C.muted, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 12 });
    });
    T.foot(s, "实现：perception/fusion.py，纯规则，六种结论各有测试用例");
    s.addNotes("识别层的总纲。被问到为什么不用一个端到端大模型，答案是四个子问题对算力、精度、输出形式的要求互相冲突。仲裁层用规则不" +
      "用模型，对应任务书把可解释性列为必须考虑的非技术因素。");
  }

  // ============================================================ L1
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.识别层 L1", "3.9　L1 目标检测：为什么用公开集训练，为什么分两级阈值",
      ["YOLO11 + 公开集权重，巡航级 0.25 与复核级 0.60 两套阈值",
      "巡航期只负担得起一路小模型；误报不在这一层消解，留给放大后的复核级"]);
    const st = [["0.9949", "mAP50", C.green], ["0.7513", "mAP50-95", C.blue],
                ["0.9967", "precision", C.blue], ["0.9976", "recall", C.blue]];
    st.forEach(([v, k, col], i) => {
      const x = M + i * 2.20;
      T.card(s, x, y0, 2.06, 1.02);
      T.stat(s, x + 0.20, y0 + 0.08, 1.7, v, k, col);
    });
    T.card(s, M + 8.86, y0, 3.52, 1.02, C.amberSoft);
    s.addText("第 1 轮 mAP50 已达 0.9759，偏高。\n需先排除训练集与验证集之间的同源增广副本。", {
      x: M + 9.06, y: y0, w: 3.16, h: 1.02, fontFace: F, fontSize: 9.5, bold: true,
      color: "8A5200", isTextBox: true, margin: 0, valign: "middle", lineSpacing: 13 });
    s.addImage({ path: IMG + "/l1_pr.png", x: M, y: y0 + 1.18, w: 3.30, h: 2.48 });
    s.addImage({ path: IMG + "/l1_cm.png", x: M + 3.46, y: y0 + 1.18, w: 3.30, h: 2.48 });
    // 左右两列同高：图片下沿 y0+3.66，槽位下沿 y0+3.86

    T.card(s, M + 6.92, y0 + 1.18, 5.46, 1.14);
    T.cardTitle(s, M + 7.14, y0 + 1.26, 5.0, "0.25 与 0.60 这两个阈值是怎么定的");
    s.addText("0.25 是巡航级的下限：再低，抑制规则挡不住的误触发会耗尽复核预算。" +
      "0.60 是复核级的下限：低于它的目标在放大后仍判不实，与其给一个不可信的结论，不如输出证据不足交人复核。" +
      "两级之差 Δconf 是复核增益的度量：Δconf 为正，说明放大后的判定比巡航时更有把握。", {
      x: M + 7.14, y: y0 + 1.56, w: 5.02, h: 0.70, fontFace: F, fontSize: 9.2,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 12.5 });
    T.slot(s, M + 6.92, y0 + 2.44, 5.46, 1.42, "待补充的实测项（明日补入）",
      ["复核级 yolo11m 训练与 mAP50 对比", "单帧耗时（≤ 33 ms）与漏检率（≤ 2 %）",
       "切换 detector: yolo 后 run_all 三轮对比，以及 --check-leak 同源副本排查结果"],
      "→ 明日由 L1 负责人提供后重新出图");

    T.card(s, M, y0 + 3.98, W - M * 2, 0.84, C.blueSoft);
    s.addText("方案比选：合成检测器 vs 公开集训练的 YOLO　|　" +
      "合成检测器零成本、可离线复现，但纹理单一，上真机必然退化；" +
      "公开集训练需注册账号并占用 GPU 工时，换来纹理多样性。" +
      "结论：权重从公开集训练，合成数据仅用于增广，理由是合成数据补的是密度分层而非纹理多样性。", {
      x: M + 0.28, y: y0 + 3.98, w: 11.85, h: 0.84, fontFace: F, fontSize: 9.8,
      color: C.navy, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 13 });
    T.foot(s, "任务书点名可用 YOLO 系列与 MobileNet，本组选 YOLO11");
    s.addNotes("L1 巡航级已训完，指标偏高。第 1 轮就到 0.9759 这个数不正常，我们自己先提出来：已写好 --check-" +
      "leak 命令排查训练集与验证集的同源增广副本，明日出结果。在结论出来之前不拿这个指标当定论。");
  }

  // ============================================================ L2
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.识别层 L2", "3.10　L2 读数：为什么仍以几何解算为默认实现",
      ["几何解算（U-Net 保留不删）",
      "门槛以上三档 P90 误差最低且置信区间不重叠；零权重、可追溯"]);
    s.addImage({ path: IMG + "/l2_reading.png", x: M, y: y0, w: 7.30, h: 4.12 });
    s.addText("灰底两档为像素密度低于 96 px 的区间。fusion.py 的 DENSITY_FLOOR_FRAC = 0.80 " +
      "在此拒绝下任何读数类结论，系统不会在这两档读数，所以这里的差异不参与比选。", {
      x: M, y: y0 + 4.16, w: 7.30, h: 0.44, fontFace: F, fontSize: 8.8,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 12 });

    s.addImage({ path: IMG + "/l2_iou.png", x: M + 7.52, y: y0, w: 4.86, h: 2.78 });

    T.card(s, M + 7.52, y0 + 2.90, 4.86, 1.70, C.greenSoft);
    T.cardTitle(s, M + 7.74, y0 + 2.98, 4.4, "读数环节采用几何解算，理由是这张图", C.green);
    s.addText("门槛以上三档，几何法 P90 稳定在 0.18 至 0.19 %FS，U-Net 为 0.36 至 0.40，" +
      "numpy 逐像素为 0.25 至 0.37，置信区间互不重叠。分割 IoU 与读数精度并不同向：" +
      "U-Net 的指针 IoU 是 0.778，读数误差反而更大，因为几何解算只需要指针的方向，不需要它的完整轮廓。", {
      x: M + 7.74, y: y0 + 3.30, w: 4.42, h: 1.22, fontFace: F, fontSize: 8.8,
      color: C.navy, isTextBox: true, margin: 0, valign: "top", lineSpacing: 12 });

    T.card(s, M, y0 + 4.70, W - M * 2, 0.92, C.blueSoft);
    s.addText("采用几何解算的三条理由　①　门槛以上三档误差最低且置信区间不重叠；" +
      "②　零权重、单次 2.2 ms，不占 NPU 配额，不与 L1、L3 争算力；" +
      "③　圆心、半径、指针角度每一步可追溯，符合任务书对可解释性的要求。" +
      "　保留 U-Net 的理由　它在几何法假设失效的样本上有优势；但合成表盘的圆心、半径与刻度分布严格满足几何法的假设，" +
      "学习法在这里没有可利用的余量，所以本结论只在合成数据上成立，真实表盘的椭圆畸变与遮挡是否构成失效，要等真实表盘误差表才能定，现在不改默认实现。", {
      x: M + 0.28, y: y0 + 4.70, w: 11.85, h: 0.92, fontFace: F, fontSize: 9.2,
      color: C.navy, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 12.5 });
    T.foot(s, "复现：bench_models --only reading --n 200　·　评测划分：val 90 个含针 ROI 中 59 个来自真实照片");
    s.addNotes("原来的图只有 24 个样本、画的是中位数，差异被采样噪声盖住，得不出结论。现在 n=200、画 P90、加了 boo" +
      "tstrap 置信区间，三条线在门槛以上分得开。有两点要说清：灰底两档系统不读数，那里的差异不参与比选；U-Net " +
      "的分割 IoU 是三者最高的 0.778，读数误差却更大，因为几何解算只用指针方向，不用完整轮廓。还要说明一个前提：" +
      "合成表盘天然满足几何法的假设，这个结论换到真实表盘上可能反过来，所以 U-Net 保留不删。");
  }

  // ============================================================ L3
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.识别层 L3", "3.11　L3 未知异常：为什么是非监督，为什么是全协方差",
      ["PaDiM 全协方差（统计法保留为零权重降级路径）",
      "外观缺陷没有公开标注；异物改变的正是通道间相关性，对角版本看不见"]);
    const rows = [[T.th("指标"), T.th("统计法\n基线·零权重"), T.th("EfficientAD\n简化蒸馏"),
                   T.th("PaDiM\n对角"), T.th("PaDiM 全协方差\n（采用）")]];
    [["误报率（正常裁片 106）", "1.9 %", "0.0 %", "3.8 %", "3.8 %"],
     ["漏报率（异常裁片 120）", "90.8 %", "100 %（分数倒挂）", "48.3 %", "3.3 %"],
     ["正常均分 / 异常均分", "0.03 / 0.36", "0.07 / 0.00", "0.04 / 0.56", "0.06 / 0.95"],
     ["权重大小（成本）", "1.6 KB", "2.8 MB", "3.1 MB", "≈ 44 MB"],
     ["单次打分 CPU（实时性）", "6 ms", "未启用", "22 ms", "26 ms"],
     ["可解释（伦理要求）", "可说明哪个通道", "否", "否", "否"],
    ].forEach((r, ri) => rows.push([
      T.td(r[0], { bold: true, fontSize: 9.5 }),
      T.td(r[1], { align: "center", fontSize: 9.5 }),
      T.td(r[2], { align: "center", fontSize: 9.5, color: C.muted }),
      T.td(r[3], { align: "center", fontSize: 9.5 }),
      T.td(r[4], { align: "center", fontSize: 9.5, bold: true,
        color: ri === 1 ? C.green : C.text, fill: { color: C.greenSoft } })]));
    T.table(s, rows, { x: M, y: y0, w: 7.60, colW: [2.30, 1.35, 1.55, 1.05, 1.35], rowH: 0.40 });

    s.addImage({ path: IMG + "/l3_score_dist.png", x: M + 7.80, y: y0, w: 4.58, h: 2.58 });

    const why = [["统计法漏报高的原因", "用马氏距离衡量全局偏离，对局部异常不敏感。异物遮挡只改变局部区域，在全局统计量上体现很弱。", C.blue],
                 ["EfficientAD 分数倒挂的原因", "简化蒸馏在正常样本上过拟合后，学生网络对异常区域的重建误差反而更小。属方法与数据规模不匹配，非超参数问题。", C.red],
                 ["全协方差有效的原因", "对角版本假设特征通道互相独立，忽略了通道间相关性；而异物改变的正是通道间的相关结构。", C.green]];
    why.forEach(([t, d, col], i) => {
      const y = y0 + 3.18 + i * 0.68;
      T.card(s, M, y, 7.60, 0.60);
      s.addText(t, { x: M + 0.20, y, w: 2.35, h: 0.60, fontFace: F, fontSize: 9.8,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 2.66, y, w: 4.80, h: 0.60, fontFace: F, fontSize: 8.6,
        color: C.muted, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 11.5 });
    });

    T.card(s, M + 7.80, y0 + 3.18, 4.58, 1.98, C.blueSoft);
    T.cardTitle(s, M + 8.02, y0 + 3.28, 4.1, "两项决定结果的实测发现");
    s.addText("1　训练裁片必须模拟运行时的检测框噪声。实测裁掉 12 % 的正常表盘，异常分由 0.5 升到 1.0，全部误报。" +
      "增广集加入抖动框后，系统实测的复核 ROI 回到正常分 0.00 至 0.09。\n\n" +
      "2　L3 与 L2 分工清晰：开位开关被 L2 判为状态异常，L3 对其外观给正常分；外观异常才由 L3 负责。", {
      x: M + 8.02, y: y0 + 3.62, w: 4.14, h: 1.48, fontFace: F, fontSize: 8.8,
      color: C.navy, isTextBox: true, margin: 0, valign: "top", lineSpacing: 12 });
    T.foot(s, "评测条件：训练 796 张正常裁片，评测 106 正常 + 120 异常，四方案阈值统一取 0.55");
    s.addNotes("四个方案在同一批样本、同一阈值下比较，结论明确。EfficientAD 那一列是失败的，数据照原样写进表里。权重大小" +
      "与打分耗时两行对应成本与实时性，可解释一行对应任务书的伦理要求。");
  }

  // ============================================================ 比选汇总
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.方案比选", "3.12　方案比选汇总：技术与非技术因素的权衡",
      "非技术因素按任务书列出的四项计：安全、成本、可靠性、伦理与社会责任");
    const rows = [[T.th("环节"), T.th("候选方案"), T.th("技术权衡"), T.th("非技术因素权衡"), T.th("结论")]];
    [["L1 检测", "① 合成检测器\n② YOLO11 公开集训练", "① 零成本可离线复现但纹理单一\n② 纹理多样但需 GPU 工时",
      "成本：② 需注册账号与显卡；可靠性：① 上真机必然退化", "采用 ②，① 保留为无权重时的降级路径"],
     ["L2 读数", "① 几何解算\n② numpy 逐像素\n③ U-Net", "P90 %FS（门槛以上）\n① 0.18–0.19　② 0.25–0.37\n③ 0.36–0.40　耗时 2.2/5/59 ms",
      "成本：③ 权重与算力开销最大；可解释：① 每一步可追溯，③ 不可", "采用 ①（n = 200、置信区间不重叠）；③ 保留，待真实表盘误差表再定"],
     ["L3 异常", "① 统计法\n② EfficientAD\n③④ PaDiM 对角 / 全协方差", "漏报 90.8 / 100 / 48.3 / 3.3 %",
      "成本：④ 权重 44 MB 最大；可解释：① 可，其余不可；可靠性：② 分数倒挂不可用", "采用 ④；① 保留为零权重降级路径"],
     ["跟踪算法", "① IoU 跟踪\n② ByteTrack（任务书点名）", "① 实现简单、零依赖\n② 对遮挡与漏检更鲁棒",
      "成本：② 引入额外依赖与调参工作量；可靠性：① 云台转动时易断链", "当前用 ①，② 列入下一阶段"],
     ["部署形态", "① 云端推理\n② 边缘推理（RK3576）", "① 算力充足\n② 受 NPU 算力限制",
      "可靠性：① 断网即失效；成本：① 持续带宽费用；安全：② 本地闭环响应更快", "采用 ②，符合任务书边缘计算定位"],
    ].forEach(r => rows.push([
      T.td(r[0], { bold: true, fontSize: 9.5, color: C.navy }),
      T.td(r[1], { fontSize: 9 }), T.td(r[2], { fontSize: 9 }),
      T.td(r[3], { fontSize: 9, color: C.red }),
      T.td(r[4], { fontSize: 9, bold: true, color: C.green })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [1.15, 2.30, 2.65, 3.35, 3.04], rowH: 0.76 });

    T.card(s, M, y0 + 4.74, W - M * 2, 0.62, C.navy);
    s.addText("表里有两行的结论是「不采用」：L3 的 EfficientAD 复现后分数倒挂，L2 的 U-Net 读数误差反而更大。" +
      "这两条连同实测数据一起留在表里，没有删。排除一个方案同样是比选的结果，也解释了最后为什么选另一个。", {
      x: M + 0.28, y: y0 + 4.74, w: 11.85, h: 0.62, fontFace: F, fontSize: 9.8,
      color: "DCE9F5", isTextBox: true, margin: 0, valign: "middle", lineSpacing: 13 });
    T.foot(s);
    s.addNotes("五个环节的比选汇总在一起看。每一行的第四列专门写非技术因素：成本、可靠性、可解释性、安全。比选后不采用同样是结论，被" +
      "排除的方案连同数据一起保留在表里。");
  }

  // ============================================================ 安全边界
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.安全边界", "3.13　安全边界：三层防线，每一层都能当场演示",
      ["参数硬限写进源码、心跳看门狗、安全事件抢占三层",
      "配置文件在现场可以被改动，参数硬限因此写在源码里，由进程结构本身保证"]);
    T.card(s, M, y0, W - M * 2, 0.72, C.navy);
    s.addText("任务书允许的高层指令：暂停巡检　·　低速前进　·　移动至观察点　·　云台转向 / 变焦　·　恢复路线　" +
      "　　急停、避障、限速的优先级高于巡检任务", {
      x: M + 0.28, y: y0, w: 11.85, h: 0.72, fontFace: F, fontSize: 11, bold: true,
      color: "FFD27F", isTextBox: true, margin: 0, valign: "middle" });
    const layers = [
      ["第一层　指令白名单与参数硬限", C.red,
       "五项校验逐条上报 PASS、FAIL 或 SKIP。参数范围硬编码在源码里，不放进配置文件，因为配置文件在现场可以被改动。白名单只含任务书列出的高层指令。",
       "pytest tests/test_gateway.py -k out_of_range -v", "越界指令被拒，并留下逐项校验的审计记录"],
      ["第二层　心跳看门狗", C.amber,
       "感知与任务进程异常退出后，网关在 1.5 s 内接管并下发「恢复路线」，使车辆按原路线走完。对应任务书「模型异常、算力不足时的安全降级」。",
       "run_all 启动后终止 mission 进程", "AI 进程终止后，车辆仍按路线走完全程"],
      ["第三层　安全事件抢占", C.blue,
       "任何时刻的安全事件都能打断正在进行的复核，200 ms 内中止并回到安全状态。对应任务书「急停、避障、限速优先级高于巡检任务」。",
       "pytest tests/test_fsm.py -k safety -v", "注入安全事件，正在进行的复核 200 ms 内中止"],
    ];
    layers.forEach(([t, col, d, cmd, res], i) => {
      const y = y0 + 0.92 + i * 1.20;
      T.card(s, M, y, W - M * 2, 1.10);
      s.addShape("rect", { x: M, y, w: 0.08, h: 1.10, fill: { color: col }, line: { color: col, width: 0 } });
      s.addText(t, { x: M + 0.26, y: y + 0.08, w: 5.5, h: 0.30, fontFace: F, fontSize: 12,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 0.26, y: y + 0.38, w: 5.6, h: 0.66, fontFace: F, fontSize: 9,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 12 });
      T.card(s, M + 6.10, y + 0.12, 6.28, 0.38, C.blueSoft);
      s.addText(cmd, { x: M + 6.24, y: y + 0.12, w: 6.0, h: 0.38, fontFace: "Courier New",
        fontSize: 8.2, color: C.navy, isTextBox: true, margin: 0, valign: "middle" });
      s.addText("现场可见结果：" + res, { x: M + 6.10, y: y + 0.56, w: 6.28, h: 0.48, fontFace: F,
        fontSize: 10, bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
    });
    T.card(s, M, y0 + 4.52, W - M * 2, 0.52, C.blueSoft);
    s.addText("所有指向底盘与云台的动作只经过 gateway 一个出口。其余三个进程即使写错代码，也没有可以调用的驱动实例。", {
      x: M + 0.28, y: y0 + 4.52, w: 11.85, h: 0.52, fontFace: F, fontSize: 10.5, bold: true,
      color: C.navy, isTextBox: true, margin: 0, valign: "middle" });
    T.foot(s, "三条演示合计不到一分钟，可在答辩现场依次执行");
    s.addNotes("回应任务书的安全要求。三条演示都能当场执行。如果对安全设计有疑问，直接演示第二条：终止 AI 进程后车辆仍按路线走完" +
      "，这一条最直观。");
  }

  // ============================================================ 控制与闭环
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.控制验证", "3.14　云台伺服：为什么必须做变焦增益调度",
      ["PID 控制量按变焦倍率缩放，ω = θ /(W · z) · u",
      "3× 变焦下同样的转角对应三倍画面位移，固定增益等于把回路增益放大三倍"]);
    s.addImage({ path: IMG + "/pid_step.png", x: M, y: y0, w: 6.20, h: 3.79 });
    const rows = [[T.th("工况"), T.th("超调"), T.th("调节时间"), T.th("结论")]];
    [["1× 广角", "3.0 %", "0.901 s", "达标"], ["3× 变焦（有调度）", "1.0 %", "1.202 s", "达标"],
     ["3× 变焦（关调度）", "37.7 %", "1.102 s", "不可接受"]].forEach((r, i) => rows.push([
      T.td(r[0], { fontSize: 9.5, bold: i === 2 }),
      T.td(r[1], { fontSize: 9.5, align: "center", bold: true, color: i === 2 ? C.red : C.green }),
      T.td(r[2], { fontSize: 9.5, align: "center" }),
      T.td(r[3], { fontSize: 9.5, align: "center", bold: true, color: i === 2 ? C.red : C.green })]));
    T.table(s, rows, { x: M + 6.42, y: y0, w: 5.96, colW: [2.20, 1.20, 1.36, 1.20], rowH: 0.40 });

    T.card(s, M + 6.42, y0 + 1.72, 5.96, 1.00, C.blueSoft);
    s.addText("为什么必须做增益调度：同样 1° 的云台转角，在 3× 变焦下对应的画面位移是 1× 的三倍。" +
      "控制量若不按倍率缩放，等效于把回路增益放大三倍，必然过冲。本系统按 ω = θ /(W · z) · u 缩放。", {
      x: M + 6.64, y: y0 + 1.72, w: 5.55, h: 1.00, fontFace: F, fontSize: 9.5,
      color: C.navy, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 13 });

    const st = [["4 + 1", "进程全链路打通", C.green], ["4 – 6", "每轮产出证据包", C.blue],
                ["1.9 – 2.3", "复核前后密度比", C.green], ["+0.44", "真缺陷组 Δconf", C.green]];
    st.forEach(([v, k, col], i) => {
      const x = M + 6.42 + (i % 2) * 3.05, y = y0 + 2.86 + Math.floor(i / 2) * 0.94;
      T.card(s, x, y, 2.89, 0.86);
      T.stat(s, x + 0.20, y, 2.5, v, k, col);
    });

    T.card(s, M, y0 + 3.94, 6.20, 0.72, C.amberSoft);
    s.addText("复核成功率两轮实测 83.3 % 与 75.0 %，均低于本组设定的 85 % 目标。" +
      "主因是证据包偶发配对失败导致该包不计入成功，已定位但尚未修复，列入下一阶段。", {
      x: M + 0.22, y: y0 + 3.94, w: 5.80, h: 0.72, fontFace: F, fontSize: 9.2, bold: true,
      color: "8A5200", isTextBox: true, margin: 0, valign: "middle", lineSpacing: 12 });
    T.foot(s, "复现：tune_pid --compare-gain-schedule　·　run_all --seconds 300　·　浏览器 127.0.0.1:8000 看台账");
    s.addNotes("课题名称是测控系统，控制部分的实测依据集中在这一页。绿色曲线的过冲是关闭增益调度后的响应。右下角的复核成功率没有达标" +
      "，原因已定位，这一条自己先讲。");
  }

  // ------------------------------------------------------------ 四
  T.toc(pres, 4, "本节回答：本方案与既有做法的区别在哪，这些区别各自解决了什么问题");

  // ============================================================ 创新点
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "四.项目特色", "4.1　项目特色与主要创新点",
      "四条各自解决一个既有巡检系统没有处理的问题");
    const N = [
      ["01", "采集参数由判读结果决定的主动复核", C.red,
       "既有巡检系统按固定参数采集。本系统把判读结果反馈回采集环节，按目标当前成像算出所需变焦倍率。",
       "对应任务书「判断图像质量—主动补拍」",
       "像素密度由 50.0 px 提高到 150.0 px，与针孔公式计算值相差 0.4 %"],
      ["02", "变焦增益调度的云台伺服", C.blue,
       "画面位移与变焦倍率成正比，固定增益 PID 在变焦后等效增益被放大。本系统按倍率缩放控制量。",
       "对应任务书「云台转向 / 变焦」高层指令",
       "3× 变焦超调 1.0 %、调节时间 1.202 s；关闭调度后超调升至 37.7 %"],
      ["03", "四类模型协同并由显式规则仲裁", C.navy,
       "四个子问题对算力、精度、输出形式的要求互相冲突。分路实现后由纯规则的 L4 仲裁，结论可逐级追溯。",
       "对应任务书「伦理与社会责任：可解释性」",
       "六种结论各有测试用例；L3 对训练集外异常的漏报由 90.8 % 降至 3.3 %"],
      ["04", "接口先冻结、驱动先抽象的无硬件开发方法", C.green,
       "五份 Schema 编码前定稿并配 51 项一致性校验；四个抽象基类隔离硬件，桩注入真机上实际存在的故障率。",
       "对应任务书「各模块保持接口对接和进度同步」",
       "端到端 300 s 闭环与全部回归测试均在无硬件条件下完成；串口协议栈由假小车逐字节验证"],
    ];
    N.forEach(([n, t, col, d, link, ev], i) => {
      const y = y0 + i * 1.20;
      T.card(s, M, y, W - M * 2, 1.10);
      s.addText(n, { x: M + 0.22, y, w: 0.56, h: 1.10, fontFace: F, fontSize: 20,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(t, { x: M + 0.86, y: y + 0.06, w: 4.90, h: 0.34, fontFace: F, fontSize: 12.5,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 0.86, y: y + 0.40, w: 4.92, h: 0.62, fontFace: F, fontSize: 8.8,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 12 });
      T.card(s, M + 5.96, y + 0.10, 2.62, 0.90, C.blueSoft);
      s.addText(link, { x: M + 6.12, y: y + 0.10, w: 2.32, h: 0.90, fontFace: F, fontSize: 9,
        bold: true, color: C.navy, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 12 });
      T.card(s, M + 8.74, y + 0.10, 3.64, 0.90);
      s.addText("实测支撑", { x: M + 8.92, y: y + 0.16, w: 3.3, h: 0.24, fontFace: F, fontSize: 9,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(ev, { x: M + 8.92, y: y + 0.40, w: 3.32, h: 0.56, fontFace: F, fontSize: 8.6,
        color: C.text, isTextBox: true, margin: 0, valign: "top", lineSpacing: 11.5 });
    });
    T.foot(s);
    s.addNotes("四条创新点分属四块：第一条是本课题的立论，第二条在控制，第三条在识别，第四条在工程方法。中间一列写明每条对应任务书的" +
      "哪项要求，右侧给实测支撑。");
  }

  // ------------------------------------------------------------ 五
  T.toc(pres, 5, "本节回答：进度到哪一步、哪些方案尚未验证、方案选择依据的文献是哪些");

  // ============================================================ 进度与不足
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "五.研究计划", "5.1　研究进度、存在的不足与下一阶段计划",
      "任务书进度安排：开题第 1 周、中期第 2 周（系统搭建、阶段检查、接口联调）、结题第 3 至 4 周");
    const rows = [[T.th("模块"), T.th("状态"), T.th("依据"), T.th("尚未完成的部分")]];
    const data = [
      ["四进程闭环与云端台账", "已完成", "端到端 300 s 可重复运行，产出 4 至 6 个证据包", "无"],
      ["接口一致性与自动化测试", "已完成", "validate 51 项全过；501 项测试，覆盖率 75 %", "无真权重用例"],
      ["安全边界三层", "已完成", "三条演示可现场执行", "无"],
      ["云台 PID 与增益调度", "已完成", "1× 超调 3.0 %，3× 超调 1.0 %，均达标", "故障率与成功率的关系曲线未做"],
      ["L2 几何读数", "已完成", "基本误差 0.469 %、线性度 0.267 % 达标", "重复性 0.321 % 超出 0.3 % 限值"],
      ["L3 未知异常", "已完成", "PaDiM 全协方差，漏报 3.3 %、误报 3.8 %", "RKNN 上板与 INT8 掉点未测"],
      ["L2 读数方案比选", "已完成", "n = 200 的 P90 比选，几何法门槛以上 0.18 至 0.19 %FS", "结论只在合成表盘成立，真实表盘误差表未做"],
      ["L1 目标检测", "进行中", "巡航级 mAP50 0.9949", "复核级未训；耗时、漏检率、切换对比未做"],
      ["目标跟踪（ByteTrack）", "未开始", "当前用 IoU 跟踪，云台转动时易断链", "任务书点名的 ByteTrack 尚未接入"],
      ["五点标定与真机联调", "未开始", "工具已实现；驱动层已由假小车验证", "缺真实标定数据；硬件未到位"],
    ];
    const col = { "已完成": [C.green, C.greenSoft], "进行中": [C.amber, C.amberSoft], "未开始": [C.muted, C.card] };
    data.forEach(r => rows.push([
      T.td(r[0], { bold: true, fontSize: 9 }),
      T.td(r[1], { align: "center", bold: true, fontSize: 9, color: col[r[1]][0], fill: { color: col[r[1]][1] } }),
      T.td(r[2], { fontSize: 9 }),
      T.td(r[3], { fontSize: 9, color: r[3] === "无" ? C.green : C.muted })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [2.55, 1.00, 4.35, 4.59], rowH: 0.335 });

    const plan = [["下一阶段（结题第 3 至 4 周）", "补齐 L1 复核级与切换对比；用真实表盘复核 L2 的读数比选结论；接入 ByteTrack；RKNN 上板与 INT8 掉点记录；修复证据包配对缺陷；补故障率与复核成功率的可靠性曲线", C.navy],
                  ["受外部条件限制的两项", "五点标定缺真实标定数据；真机联调缺硬件。两项均已在驱动层与工具层预留接口，条件具备后可直接接入", C.muted]];
    plan.forEach(([t, d, c], i) => {
      const y = y0 + 3.92 + i * 0.66;
      T.card(s, M, y, W - M * 2, 0.60);
      s.addText(t, { x: M + 0.22, y, w: 3.15, h: 0.60, fontFace: F, fontSize: 10,
        bold: true, color: c, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 3.52, y, w: 8.85, h: 0.60, fontFace: F, fontSize: 9,
        color: C.muted, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 12 });
    });
    T.foot(s, "状态判定标准：能产出可复现的实测数据才记为已完成");
    s.addNotes("状态判定用统一标准：能产出可复现的实测数据才记为完成。两项未开始的原因自己先讲，其中 ByteTrack 是任务书点" +
      "名的算法，目前用的是 IoU 跟踪，不回避。");
  }

  // ============================================================ 参考文献
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "五.文献支撑", "5.2　参考文献与综述对选题、方案的支撑",
      "共 18 篇（部），按目标检测与跟踪、异常检测、分割与几何、部署与控制四类归并");
    const G = [
      ["目标检测与多目标跟踪", C.red, [
        "[1] Redmon J, et al. You Only Look Once: Unified, Real-Time Object Detection. CVPR, 2016.",
        "[2] Ultralytics. YOLO11 官方文档与开源实现. 2024.",
        "[3] Zhang Y, et al. ByteTrack: Multi-Object Tracking by Associating Every Detection Box. ECCV, 2022.",
        "[4] Bewley A, et al. Simple Online and Realtime Tracking. ICIP, 2016.",
        "[5] Howard A G, et al. MobileNets: Efficient CNNs for Mobile Vision Applications. arXiv:1704.04861, 2017."]],
      ["非监督异常检测", C.green, [
        "[6] Defard T, et al. PaDiM: a Patch Distribution Modeling Framework for Anomaly Detection. ICPR, 2021.",
        "[7] Batzner K, et al. EfficientAD: Accurate Visual Anomaly Detection at Millisecond-Level Latencies. WACV, 2024.",
        "[8] Roth K, et al. Towards Total Recall in Industrial Anomaly Detection. CVPR, 2022.",
        "[9] Bergmann P, et al. MVTec AD: A Comprehensive Real-World Dataset for Unsupervised AD. CVPR, 2019."]],
      ["分割、几何解算与读数", C.blue, [
        "[10] Ronneberger O, et al. U-Net: Convolutional Networks for Biomedical Image Segmentation. MICCAI, 2015.",
        "[11] Hartley R, Zisserman A. Multiple View Geometry in Computer Vision. 2nd ed. Cambridge Univ. Press, 2004.",
        "[12] Fitzgibbon A, et al. Direct Least Square Fitting of Ellipses. IEEE TPAMI, 1999.",
        "[13] Bradski G. The OpenCV Library. Dr. Dobb's Journal of Software Tools, 2000.",
        "[14] 百度 PaddleX. 工业表计读数产业实践方案与配套数据集. 开源文档."]],
      ["边缘部署、机器人与控制", C.navy, [
        "[15] Rockchip. RK3576 Datasheet 与 RKNN-Toolkit2 用户指南. 2024.",
        "[16] Jacob B, et al. Quantization and Training of Neural Networks for Integer-Arithmetic-Only Inference. CVPR, 2018.",
        "[17] Quigley M, et al. ROS: an Open-Source Robot Operating System. ICRA Workshop on Open Source Software, 2009.",
        "[18] Åström K J, Hägglund T. PID Controllers: Theory, Design, and Tuning. 2nd ed. ISA, 1995."]],
    ];
    G.forEach(([t, col, items], i) => {
      const x = M + (i % 2) * 6.30, y = y0 + Math.floor(i / 2) * 1.90;
      T.card(s, x, y, 6.05, 1.80);
      s.addText(t, { x: x + 0.22, y: y + 0.06, w: 5.6, h: 0.28, fontFace: F, fontSize: 11,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(items.join("\n"), { x: x + 0.22, y: y + 0.36, w: 5.62, h: 1.36, fontFace: F,
        fontSize: 7.8, color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 11.5 });
    });
    T.card(s, M, y0 + 3.92, W - M * 2, 0.80, C.navy);
    s.addText("综述如何支撑选题与方案：[1][2][5] 支撑巡航期轻量检测的骨干选择；[3][4] 说明云台转动导致跟踪断链的成因，" +
      "并给出下一阶段接入 ByteTrack 的依据；[6][7][8][9] 构成 L3 四方案比选的方法来源，其中 [7] 经实测未采用；" +
      "[10][11][12] 分别支撑分割替换、针孔投影与椭圆拟合三个环节；[15][16] 支撑 RKNN 部署与 INT8 量化的掉点评估；[18] 支撑变焦增益调度的整定方法。", {
      x: M + 0.28, y: y0 + 3.92, w: 11.85, h: 0.80, fontFace: F, fontSize: 9,
      color: "DCE9F5", isTextBox: true, margin: 0, valign: "middle", lineSpacing: 12.5 });
    T.foot(s);
    s.addNotes("18 篇分四类，任务书列出的五类参考资料都覆盖到了。底部那段说明每组文献支撑方案的哪个环节，不是简单罗列。其中 Ef" +
      "ficientAD 是复现后未采用的，也写明了。");
  }

  // ============================================================ 结束
  {
    const s = T.slide(pres);
    const TPL = IMG + "/tpl";
    s.addImage({ path: TPL + "/cover_photo.jpg", x: 0, y: 0, w: 13.42, h: 6.56 });
    s.addImage({ path: TPL + "/cover_wave.png", x: -0.04, y: 3.39, w: 13.48, h: 4.15 });
    s.addImage({ path: TPL + "/cover_logo.png", x: 0.26, y: 0.25, w: 3.38, h: 0.87 });
    s.addText("敬请各位老师批评指正", { x: 1.05, y: 4.22, w: 11.23, h: 0.86, align: "center",
      fontFace: F, fontSize: 30, bold: true, color: C.white, isTextBox: true, margin: 0, valign: "middle" });
        s.addText("基于 RK3576 边缘计算的\n无人车主动式 AI 巡检系统设计", { x: 5.30, y: 6.24, w: 2.80, h: 1.00,
      align: "center", fontFace: F, fontSize: 10.5, color: C.navy, isTextBox: true,
      margin: 0, valign: "middle", lineSpacing: 15 });
    s.addNotes("结束页。如果时间允许，主动提出现场演示，选终止 AI 进程后车辆仍按路线走完那条。");
  }
};
