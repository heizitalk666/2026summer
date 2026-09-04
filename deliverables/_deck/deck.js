// 中期答辩正文。版式取自学院模板，指标与要求以《测控系统综合实训课题任务书》为准。
const T = require("./theme");
const { C, F, W, H, M } = T;

module.exports = function (pres, IMG) {
  const TPL = IMG + "/tpl";

  // ============================================================ 1 封面
  {
    const s = T.slide(pres);
    s.addImage({ path: TPL + "/cover_photo.jpg", x: 0, y: 0, w: 13.42, h: 6.56 });
    s.addImage({ path: TPL + "/cover_wave.png", x: -0.04, y: 3.39, w: 13.48, h: 4.15 });
    s.addImage({ path: TPL + "/cover_logo.png", x: 0.26, y: 0.25, w: 3.38, h: 0.87 });
    s.addImage({ path: TPL + "/cover_seal.png", x: 0.26, y: 1.37, w: 1.61, h: 1.04 });
    s.addText("中期答辩", { x: 9.70, y: 0.26, w: 3.51, h: 0.87, align: "right",
      fontFace: F, fontSize: 16, bold: true, color: C.white, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("基于 RK3576 边缘计算的无人车主动式 AI 巡检系统设计", {
      x: 0.50, y: 3.90, w: 12.33, h: 0.90, align: "center", fontFace: F, fontSize: 29,
      bold: true, color: C.white, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("测控系统综合实训　·　实训地点 A210　·　指导教师 陈震", {
      x: 2.40, y: 4.84, w: 8.53, h: 0.56, align: "center", fontFace: F, fontSize: 14.5,
      color: "D8E6F2", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("汇报人：吴明哲\n2026 年 9 月", { x: 5.53, y: 6.30, w: 2.34, h: 0.92,
      align: "center", fontFace: F, fontSize: 12.5, color: C.navy, isTextBox: true,
      margin: 0, valign: "middle", lineSpacing: 19 });
    s.addNotes("开场一句：本课题按任务书要求，基于现有无人车底盘与 RK3576 边缘主机，开发主动式 AI 巡检系统，实现「发现异常、判断图像质量、主动补拍、形成证据、上报复核」的完整闭环。中期阶段的任务是系统搭建、阶段检查与接口联调，本次汇报围绕这三项展开，重点讲我们采用的方案和每一步的选择理由。");
  }

  // ============================================================ 2 目录（兼第一部分分节页）
  T.toc(pres, 1, "按学院答辩要求分五部分。本次汇报的重点是第三部分：我们采用的技术方案，以及每一步为什么这样选");

  // ============================================================ 研究背景
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "一.研究背景", "1.1　课题来源与任务要求",
      "任务书指出：园区、管廊、光伏电站、电力站房等场景下，固定点位视频监控难以覆盖全部设备和动态缺陷");
    T.card(s, M, y0, 6.05, 1.62, C.navy);
    s.addText("任务书对预期成果的表述", { x: M + 0.26, y: y0 + 0.14, w: 5.5, h: 0.30,
      fontFace: F, fontSize: 13, bold: true, color: "FFD27F", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("「一套可运行的主动式 AI 巡检系统，实现『发现异常—判断图像质量—主动补拍—" +
      "形成证据—上报复核』的完整闭环，首版可先以单路 1080p RGB 相机和有限缺陷类别达成稳定闭环。」", {
      x: M + 0.26, y: y0 + 0.50, w: 5.55, h: 0.98, fontFace: F, fontSize: 11.2,
      color: "DCE9F5", isTextBox: true, margin: 0, valign: "top", lineSpacing: 16.5 });

    T.card(s, M + 6.30, y0, 6.05, 1.62);
    T.cardTitle(s, M + 6.56, y0 + 0.14, 5.5, "任务书列出的八项主要研究内容");
    s.addText("目标检测、已知缺陷识别、未知异常检测和图像质量评价；\n" +
      "基于目标跟踪的异常目标持续锁定；\n" +
      "主动复核状态机设计，实现停车、居中、变焦、多角度补拍和恢复巡检路线；\n" +
      "巡检事件证据包、离线缓存、告警分级和远程上传；\n" +
      "云端人工复核、模型版本管理和巡检结果闭环；\n" +
      "系统实时性、识别效果、网络中断降级和运行稳定性测试。", {
      x: M + 6.56, y: y0 + 0.50, w: 5.55, h: 0.98, fontFace: F, fontSize: 9.5,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 13 });

    s.addText("任务书列出的七项相互制约的技术因素，本课题必须在方案设计中明确取舍", {
      x: M, y: y0 + 1.78, w: 12.4, h: 0.32, fontFace: F, fontSize: 13.5, bold: true,
      color: C.navy, isTextBox: true, margin: 0, valign: "middle" });
    const pairs = [
      ["边缘算力", "模型复杂度", "巡航期只跑得起小模型"],
      ["识别准确率", "实时性", "高精度模型无法 30 Hz 运行"],
      ["图像分辨率", "存储与传输带宽", "全程高清回传不可行"],
      ["主动补拍效果", "车辆安全", "停车对准必须受安全约束"],
      ["本地处理", "云端分析", "断网时仍要能工作"],
      ["续航时间", "计算功耗", "算力开销直接影响续航"],
      ["巡检效率", "证据完整性", "证据越全，单点耗时越长"],
    ];
    pairs.forEach(([a, b, d], i) => {
      const x = M + (i % 4) * 3.12, y = y0 + 2.18 + Math.floor(i / 4) * 1.30;
      T.card(s, x, y, 2.96, 1.18);
      s.addText(a, { x: x + 0.16, y: y + 0.10, w: 1.30, h: 0.30, align: "center", fontFace: F,
        fontSize: 11.5, bold: true, color: C.navy, isTextBox: true, margin: 0, valign: "middle" });
      s.addText("↔", { x: x + 1.42, y: y + 0.10, w: 0.24, h: 0.30, align: "center", fontFace: F,
        fontSize: 11.5, bold: true, color: C.red, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(b, { x: x + 1.60, y: y + 0.10, w: 1.24, h: 0.30, align: "center", fontFace: F,
        fontSize: 11.5, bold: true, color: C.navy, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: x + 0.16, y: y + 0.46, w: 2.66, h: 0.62, align: "center", fontFace: F,
        fontSize: 10.2, color: C.muted, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 14.5 });
    });
    T.card(s, M + 9.36, y0 + 3.48, 2.96, 1.18, C.blueSoft);
    s.addText("这七项取舍构成本课题的设计空间。\n第三部分的总体方案与逐层选型，都是在这七项之间做的取舍。", {
      x: M + 9.52, y: y0 + 3.48, w: 2.66, h: 1.18, align: "center", fontFace: F, fontSize: 10.8,
      bold: true, color: C.navy, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 15.5 });
    T.foot(s);
    s.addNotes("七项相互制约因素是任务书原文，本课题的设计空间就由它们框定。任务书写明「不直接指定具体技术方案，要求学生调研并比选后" +
      "确定方案」，所以后面每一路都做了比选，其中有几路的结论是不采用。");
  }

  // ============================================================ 国内外现状
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "一.研究背景", "1.2　国内外现状与本课题定位",
      "配电室与站房巡检已有成熟产品，本课题要补的是它们共同回避的一段");
    const rows = [[T.th("方案类别"), T.th("代表做法"), T.th("已能解决"), T.th("尚未解决")]];
    [["固定式在线监测", "每台设备旁装传感器与摄像头，接入综合监控",
      "连续监测、响应快、无运动部件", "改造成本随设备数线性增长；老站点布线困难；视角固定，装错位置就读不到"],
     ["轨道式巡检机器人", "沿预埋导轨往复运行，定点拍摄并回传",
      "路径确定、定位精度高、供电稳定", "轨道属土建改造；路径不可变更；仍以录像回传为主，读数依赖后端人工或离线算法"],
     ["轮式巡检机器人\n（本课题）", "自主导航至巡检位，云台对准后采集",
      "无需土建改造、路径可重规划", "定位与云台误差直接影响成像；算力受限，无法全程按高精度采集"],
    ].forEach((r, i) => rows.push([
      T.td(r[0], { bold: true, fontSize: 10.8, color: i === 2 ? C.red : C.navy }),
      T.td(r[1], { fontSize: 10.5 }), T.td(r[2], { fontSize: 10.5, color: C.green }),
      T.td(r[3], { fontSize: 10.5, color: C.muted })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [1.95, 3.05, 2.75, 4.74], rowH: 0.82 });

    T.card(s, M, y0 + 3.40, W - M * 2, 1.06, C.navy);
    s.addText("三类方案的共同缺口：把采集与判读当成两件事", {
      x: M + 0.30, y: y0 + 3.48, w: 11.9, h: 0.32, fontFace: F, fontSize: 14.5, bold: true,
      color: "FFD27F", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("现有方案按固定参数采集，再把图像交给后端判读。采集时无法判断这一帧是否够用，" +
      "而判读阶段已经无法补救：表计成像不足时，后端算法读不出所需精度，因为信息在光学环节已经丢失。" +
      "任务书要求的「判断图像质量、主动补拍」正是针对这一点。", {
      x: M + 0.30, y: y0 + 3.82, w: 11.9, h: 0.60, fontFace: F, fontSize: 11.2,
      color: "DCE9F5", isTextBox: true, margin: 0, valign: "top", lineSpacing: 16.5 });

    const pos = [["本课题的做法", "把判读结果反馈回采集环节：检出可疑目标后停车、对准、变焦、重新采集，使采集参数由目标本身决定", C.red],
                 ["与既有方案的关系", "不替代固定式与轨道式，而是补上「采集参数可随目标调整」这一能力，适用于设备分散、不便改造的站房", C.blue]];
    pos.forEach(([t, d, col], i) => {
      const x = M + i * 6.30;
      T.card(s, x, y0 + 4.60, 6.05, 0.82);
      s.addText(t, { x: x + 0.24, y: y0 + 4.66, w: 5.6, h: 0.28, fontFace: F, fontSize: 12,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: x + 0.24, y: y0 + 4.94, w: 5.60, h: 0.44, fontFace: F, fontSize: 10.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14.5 });
    });
    T.foot(s);
    s.addNotes("现状部分。三类方案各有适用场景，本课题不取代它们，补的是采集参数可随目标调整这一项能力。任务书第一页写明「是否有成熟" +
      "技术方案：否」「是否需要方案比选：是」，这是我们逐路做比选的依据。");
  }

  // ------------------------------------------------------------ 二
  T.toc(pres, 2, "本节回答：核心技术难题是什么，研究目标如何量化，本阶段达到了什么");

  // ============================================================ 关键难题
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "二.关键难题", "2.1　核心难题：成像像素密度决定读数精度",
      "同一块压力表，巡航时与复核时的成像对比");
    s.addImage({ path: IMG + "/zoom_compare.png", x: M, y: y0, w: 12.49, h: 3.51 });
    const st = [["50.0 px", "巡航态 1× 实测框宽", C.red], ["150.0 px", "复核态 3× 实测框宽", C.green],
                ["0.4 %", "实测与针孔公式的偏差", C.blue], ["120 px", "本组设定的读数下限", C.navy]];
    st.forEach(([v, k, col], i) => {
      const x = M + i * 2.36;
      T.stat(s, x, y0 + 3.66, 2.25, v, k, col);
    });
    T.card(s, M + 9.52, y0 + 3.60, 2.83, 1.02, C.blueSoft);
    s.addText("针孔公式算得 49.8 / 149.5 px，\n说明虚拟场景在光学上与公式自洽，\n其上测得的精度指标可作依据。", {
      x: M + 9.70, y: y0 + 3.60, w: 2.50, h: 1.02, fontFace: F, fontSize: 10.2, bold: true,
      color: C.navy, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 14.5 });
    T.foot(s, "复现：python -m patrol.tools.viewer --demo-zoom　·　光学公式集中在 patrol/scene/optics.py");
    s.addNotes("左边巡航态 50 像素，指针无法分辨；右边 3 倍变焦后 150 像素，刻度可读。实测框宽与针孔投影公式的计算值相差" +
      " 0.4 %，说明虚拟场景在光学上自洽，在它上面测出的精度指标有依据。120 px 这个下限由本组按读数精度要求推导" +
      "，任务书没有规定。");
  }

  // ============================================================ 研究目标与结果
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "二.研究目标", "2.2　研究目标、量化指标与本阶段结果",
      "任务书规定的是能力要求，没有给出数值；下表的量化指标由本组按工业测量惯例设定");
    const rows = [[T.th("任务书要求的能力"), T.th("本组设定的量化指标"), T.th("本阶段实测"), T.th("状态")]];
    const data = [
      ["主动补拍：发现异常后改变采集参数", "复核后成像放大 ≥ 1.5 倍", "像素密度比 1.9 至 2.3", "达成"],
      ["目标检测与已知缺陷识别", "巡航级 mAP50 ≥ 0.70", "0.9949（巡航级）", "达成"],
      ["图像质量评价与读数", "基本误差 ≤ 0.5 % FS；线性度 ≤ 0.4 % FS", "0.455 %；0.302 %", "达成"],
      ["", "重复性 ≤ 0.3 % FS", "0.309 % FS", "未达成"],
      ["云台转向与变焦（高层指令）", "超调 ≤ 10 %，调节时间 ≤ 1.5 s", "3×：1.0 %，1.101 s", "达成"],
      ["未知异常检测（不依赖缺陷标注）", "训练集外异常可检出", "漏报 3.3 %，误报 3.8 %", "达成"],
      ["AI 不得直接控制底层执行机构", "三层安全边界可现场验证", "三条演示均可当场执行", "达成"],
      ["证据包、离线缓存与远程上传", "断网可缓存，恢复后补传", "断点续传与指数退避已实现", "达成"],
    ];
    const ok = { "达成": [C.green, C.greenSoft], "未达成": [C.red, C.redSoft] };
    data.forEach(r => rows.push([
      T.td(r[0], { fontSize: 10.2, bold: true }),
      T.td(r[1], { fontSize: 10.2, color: C.blue }),
      T.td(r[2], { fontSize: 10.2, bold: true, color: ok[r[3]][0] }),
      T.td(r[3], { align: "center", bold: true, fontSize: 10.2,
        color: ok[r[3]][0], fill: { color: ok[r[3]][1] } })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [4.05, 3.55, 3.09, 1.80], rowH: 0.415 });

    const src2 = [
      ["表中的量化指标从何而来", C.navy,
       "基本误差 0.5 % FS 按工业压力表 0.5 级的允许基本误差设定，线性度与重复性是本组在该总限值下分配的分项限值。" +
       "120 px 下限由精度反推：圆盘直径不足 120 px 时，指针角度的像素量化误差已超过 0.5 % FS。" +
       "放大倍数不取固定值，按目标当前像素密度实时算出。"],
      ["唯一未达成的一项及其原因", C.red,
       "重复性 0.309 % FS 超出本组设定的 0.3 %，43 次独立标定里 29 次不合格，是系统性的。" +
       "原因是几何解算依赖指针尖端的像素级定位，角度波动与指针指向强相关。" +
       "原设想用学习分割替换该环节，比选后否掉——U-Net 读数反而不如几何法。留作待办。"],
    ];
    src2.forEach(([t, col, d], i) => {
      const x = M + i * 6.30;
      T.card(s, x, y0 + 3.92, 6.05, 1.16);
      s.addText(t, { x: x + 0.24, y: y0 + 4.00, w: 5.6, h: 0.28, fontFace: F, fontSize: 12,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: x + 0.24, y: y0 + 4.30, w: 5.60, h: 0.72, fontFace: F, fontSize: 9.8,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 13.5 });
    });
    T.foot(s, "表中数值为本组按工业测量惯例设定；实测值取自虚拟试验台，桩按真机故障率注入，真值不提供给感知侧");
    s.addNotes("任务书规定的是能力要求，没有给出 0.5 % FS 这类数值，表里的量化指标由本组按工业测量惯例设定。八项中七项达成" +
      "。重复性 0.309 % FS 超出本组自己设的 0.3 % 限值，43 次独立标定里 29 次不合格，这一项写在表里，不回避。");
  }

  // ------------------------------------------------------------ 三
  T.toc(pres, 3, "本节的主线：先讲采用的总体方案与否定的两种备选，再逐层讲每个环节选了什么、为什么这样选");

  // ============================================================ 总体方案与选择理由
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.总体方案", "3.1　采用的总体方案，以及为什么不是另外两种",
      "任务书第一页写明「是否有成熟技术方案：否」「是否需要方案比选：是」，总体方案本身也经过比选");

    T.card(s, M, y0, W - M * 2, 1.06, C.navy);
    s.addText("本课题采用的总体方案：两段式主动复核", { x: M + 0.28, y: y0 + 0.08, w: 11.85, h: 0.32,
      fontFace: F, fontSize: 14, bold: true, color: "FFD27F", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("车辆以 30 Hz 巡航，只用轻量检测模型做普查；一旦检出可疑目标，向车辆控制层下发「暂停巡检」「移动至观察点」" +
      "「云台转向 / 变焦」等高层指令，把目标成像放大到可测量的尺度后再判读，得出结论后下发「恢复路线」继续巡检。" +
      "采集参数由判读结果决定，不是事先固定。", {
      x: M + 0.28, y: y0 + 0.42, w: 11.85, h: 0.56, fontFace: F, fontSize: 11.2,
      color: "DCE9F5", isTextBox: true, margin: 0, valign: "top", lineSpacing: 16.5 });

    const P = [
      ["方案一", "全程按复核精度采集", "否定", C.red,
       "整条路线都用高分辨率、高倍率与复杂模型采集判读，不区分巡航与复核。",
       "任务书列出的三项制约同时不满足：边缘算力跑不动 30 Hz 的高精度模型；全程高清回传超出存储与传输带宽；" +
       "持续满算力直接压缩续航。三项里任何一项都足以否定它。",
       "违反「算力↔模型复杂度」「分辨率↔带宽」「续航↔功耗」"],
      ["方案二", "固定参数采集，判读交后端", "否定", C.red,
       "按预设位姿与焦距拍摄回传，由云端算法或人工事后判读。这是现有轨道式、固定式产品的普遍做法。",
       "采集时无法判断这一帧是否够用，而表计成像不足时信息在光学环节就已经丢失，后端再强的算法也读不回来；" +
       "且断网即失效。任务书要求「判断图像质量、主动补拍」正是针对这一点。",
       "不满足任务书「判断图像质量—主动补拍」"],
      ["方案三", "两段式主动复核（采用）", "采用", C.green,
       "巡航期只做检出不做测量，复核期才停车、对准、变焦、重新采集并判读，算力与带宽只花在被判为可疑的航点上。",
       "三项制约同时缓解：30 Hz 只跑轻量模型；高清数据只在复核瞬间产生；放大倍数按目标当前像素密度算出，" +
       "不足就补拍、够了就走，单点耗时可控，巡检效率与证据完整性的取舍因此变成一个可以事先算出来的量。",
       "与任务书对预期成果的表述逐条对应"],
    ];
    P.forEach(([no, t, tag, col, how, why, link], i) => {
      const x = M + i * 4.22;
      T.card(s, x, y0 + 1.22, 4.05, 2.30, tag === "采用" ? C.greenSoft : null);
      s.addText(no, { x: x + 0.22, y: y0 + 1.30, w: 0.90, h: 0.30, fontFace: F, fontSize: 11.5,
        bold: true, color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
      s.addShape("roundRect", { x: x + 3.10, y: y0 + 1.30, w: 0.72, h: 0.28, rectRadius: 0.05,
        fill: { color: col }, line: { color: col, width: 0.5 } });
      s.addText(tag, { x: x + 3.10, y: y0 + 1.30, w: 0.72, h: 0.28, align: "center", valign: "middle",
        fontFace: F, fontSize: 10.2, bold: true, color: C.white, isTextBox: true, margin: 0 });
      s.addText(t, { x: x + 0.22, y: y0 + 1.62, w: 3.62, h: 0.32, fontFace: F, fontSize: 13,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText("做法　" + how, { x: x + 0.22, y: y0 + 1.96, w: 3.62, h: 0.52, fontFace: F,
        fontSize: 9.5, color: C.text, isTextBox: true, margin: 0, valign: "top", lineSpacing: 13 });
      s.addText((tag === "采用" ? "采用理由　" : "否定理由　") + why, {
        x: x + 0.22, y: y0 + 2.50, w: 3.62, h: 0.76, fontFace: F, fontSize: 9.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 13 });
      s.addText(link, { x: x + 0.22, y: y0 + 3.22, w: 3.62, h: 0.24, fontFace: F, fontSize: 9.2,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
    });

    s.addText("方案三定下来之后，剩下的研究内容是三个子问题，第三部分按这个顺序展开", {
      x: M, y: y0 + 3.62, w: 12.4, h: 0.30, fontFace: F, fontSize: 12.5, bold: true,
      color: C.navy, isTextBox: true, margin: 0, valign: "middle" });
    const Q = [
      ["什么时候该停车复核", "十状态机 + 复核预算 + 三条抑制规则", "3.3",
       "停车复核会占用巡检时间，所以要先算出这一趟还能停几次，也要避免在同一处重复停车。", C.blue],
      ["停下来怎么把目标放大到位", "针孔几何前馈 + 变焦增益调度 PID", "3.14",
       "变焦后同样的云台转角对应的画面位移会成倍放大，固定增益的回路会过冲，目标对不准就无法放大成像。", C.red],
      ["放大之后凭什么下结论", "四类模型分工 + 显式规则仲裁", "3.8、3.12",
       "四个子问题对算力、精度与输出形式的要求互相冲突，只能分路实现；结论需要逐条追溯，所以仲裁用规则而非模型。", C.navy],
    ];
    Q.forEach(([t, how, ref, why, col], i) => {
      const x = M + i * 4.22;
      T.card(s, x, y0 + 3.98, 4.05, 1.22);
      s.addShape("rect", { x, y: y0 + 3.98, w: 0.07, h: 1.22, fill: { color: col }, line: { color: col, width: 0 } });
      s.addText(t, { x: x + 0.24, y: y0 + 4.04, w: 2.46, h: 0.30, fontFace: F, fontSize: 11.5,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText("见 " + ref, { x: x + 2.62, y: y0 + 4.04, w: 1.24, h: 0.30, align: "right", fontFace: F,
        fontSize: 10, bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(how, { x: x + 0.24, y: y0 + 4.32, w: 3.62, h: 0.26, fontFace: F, fontSize: 10,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(why, { x: x + 0.24, y: y0 + 4.60, w: 3.62, h: 0.54, fontFace: F, fontSize: 9.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 13 });
    });
    T.foot(s);
    s.addNotes("先用一句话说清方案：采集参数由判读结果决定，不是事先固定。再讲另外两种为什么不行。方案一被任务书的三项制约同时否掉；" +
      "方案二的问题是信息在光学环节就丢了，后端补不回来。最后引出方案三带来的三个子问题，第三部分剩下的页面逐个回答。被问到" +
      "为什么这么做，答案在这一页。");
  }

  // ============================================================ 决策链
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.决策链", "3.2　决策链：从任务书的一条约束，到一处具体做法",
      ["把七项相互制约因素逐条落成做法", "每一条都要说得出为什么这么做，也要指得出用什么办法验证它成立"]);
    const rows = [[T.th("任务书列出的约束"), T.th("我们的做法"), T.th("为什么这么做"),
                   T.th("怎么验证它成立"), T.th("见页")]];
    [["边缘算力 ↔ 模型复杂度\n识别准确率 ↔ 实时性",
      "巡航与复核分两段，两套模型、两套阈值",
      "高精度模型跑不到 30 Hz；算力只花在被判为可疑的航点",
      "复核前后像素密度比实测 1.9 至 2.3", "3.1"],
     ["AI 不得直接控制转向、\n电机扭矩与制动力",
      "驱动实例集中在网关一个进程，且不加载模型",
      "放在同一进程里，一次段错误会同时终止感知与控制两侧",
      "终止 AI 进程后车辆仍按原路线走完", "3.13"],
     ["巡检效率 ↔ 证据完整性",
      "复核预算 N_max 加三条抑制规则",
      "停车复核占用巡检时间，需要事先算出这一趟能停几次",
      "一轮 300 s 产出 4 至 6 个证据包，路线走完", "3.3"],
     ["图像分辨率 ↔ 传输带宽\n本地处理 ↔ 云端分析",
      "判读放在边缘，高清数据只在复核瞬间产生",
      "全程高清回传超出带宽；判读靠云端则断网即失效",
      "断网可缓存，恢复后断点续传与指数退避", "3.5"],
     ["伦理与社会责任：\nAI 误判与可解释性",
      "四路模型的结论交纯规则的 L4 仲裁",
      "每条结论附 reasons 字段可逐级追溯，模型给不出这种依据",
      "六种结论各有测试用例", "3.8"],
     ["各模块接口对接\n与进度同步",
      "五份 Schema 编码前冻结 + 57 项校验",
      "四人并行开发，联调时字段必须对得上，不能边写边改",
      "validate 57 项全过，11 条反例全部被拦下", "3.4"],
    ].forEach(r => rows.push([
      T.td(r[0], { bold: true, fontSize: 10, color: C.navy }),
      T.td(r[1], { fontSize: 10 }),
      T.td(r[2], { fontSize: 10, color: C.muted }),
      T.td(r[3], { fontSize: 10, color: C.green }),
      T.td(r[4], { fontSize: 10, bold: true, align: "center", color: C.red })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [2.45, 2.55, 3.75, 2.74, 1.00], rowH: 0.62 });

    T.card(s, M, y0 + 4.62, W - M * 2, 0.64, C.navy);
    s.addText("同一条约束下都有别的解法：算力紧张可以换更小的模型，不分两段；安全边界可以写成开发约定，不做进程隔离；" +
      "接口可以边写边改，不先冻结。这六行记的是我们放弃这些做法的原因。", {
      x: M + 0.28, y: y0 + 4.62, w: 11.85, h: 0.64, fontFace: F, fontSize: 10.8,
      color: "DCE9F5", isTextBox: true, margin: 0, valign: "middle", lineSpacing: 15 });
    T.foot(s);
    s.addNotes("表格从左到右读是一条完整的推理：任务书给了什么约束、我们据此做了什么、为什么这么做、用什么办法证明它成立。时间紧就只" +
      "讲这一页；要追某一条，按最右列翻到对应页。左边一列是任务书原文列出的相互制约因素，没有改写。");
  }

  // ============================================================ 复核流程（状态机）
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.复核流程", "3.3　什么时候该停车复核：十状态机、复核预算与三条抑制规则",
      ["十状态机 + 复核预算 N_max + 三条抑制规则",
      "停车复核占用巡检时间，要先算出这一趟能停几次，也不能在同一处重复停"]);
    const steps = [
      ["CRUISE", "30 Hz 巡航\nL1 小模型扫描", C.blue],
      ["SUSPECT", "连续三帧同一目标\n才确认，防抖动", C.blue],
      ["HALT_REQ", "下发「暂停巡检」\n网关五项校验", C.red],
      ["AIM", "针孔几何前馈\n+ PID 残差闭环", C.red],
      ["ZOOM", "按目标像素密度\n算倍率并变焦", C.red],
      ["VERIFY", "四路模型推理\nL4 显式仲裁", C.green],
      ["PACK", "证据包组装\nbefore/after 配对", C.green],
      ["RESUME", "下发「恢复路线」\n写入抑制表", C.blue],
    ];
    const bw = 1.50, gap = 0.075;
    steps.forEach(([n, d, col], i) => {
      const x = M + i * (bw + gap);
      T.card(s, x, y0, bw, 1.94);
      s.addShape("rect", { x, y: y0, w: bw, h: 0.32, fill: { color: col }, line: { color: col, width: 0.5 } });
      s.addText(n, { x, y: y0, w: bw, h: 0.32, align: "center", valign: "middle",
        fontFace: F, fontSize: 10.2, bold: true, color: C.white, isTextBox: true, margin: 0 });
      s.addText(d, { x: x + 0.08, y: y0 + 0.40, w: bw - 0.16, h: 1.44, align: "center",
        fontFace: F, fontSize: 10.2, color: C.text, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 15.5 });
      if (i < steps.length - 1) s.addText("›", { x: x + bw - 0.02, y: y0 + 0.70, w: 0.14, h: 0.5,
        align: "center", fontFace: F, fontSize: 14.5, bold: true, color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
    });
    s.addText("另有 ABORT 与 ERROR 两个状态，合计十个。每个状态都定义了超时转移，不存在没有出边的状态。", {
      x: M, y: y0 + 2.02, w: 12.4, h: 0.28, fontFace: F, fontSize: 10.8,
      color: C.muted, isTextBox: true, margin: 0, valign: "middle" });

    const notes = [
      ["复核预算：停得起几次，出发前就算清楚", "N_max = ⌊(T_max − L/v) / T_r⌋",
       "任务书把「巡检效率↔证据完整性」列为必须取舍的一项，这条式子把它变成可计算的量：" +
       "按路线长度、车速与单次复核耗时，算出这一趟能停几次，超出预算的目标顺延到下一轮，不会走到一半才发现超时。", C.navy],
      ["三条抑制规则：不在同一处反复停车", "航点去重 / 定位失效 / 恢复静默",
       "同一目标会被连续多帧检出，不加抑制，车辆会在同一航点反复停车。三条规则各管一种情形：" +
       "同一航点不重复停；定位丢失后不再触发复核，因为此时停车位置本身已不可信；恢复巡航后设静默期，避免刚起步又被同一目标触发。", C.blue],
      ["安全事件抢占：复核过程随时可被打断", "200 ms 内中止正在进行的复核",
       "任务书规定急停、避障、限速的优先级高于巡检任务。复核期间车辆停在路线上、云台在转动，这段时间尤其需要能被打断。" +
       "抢占逻辑写在状态机里，由状态机本身保证，不依赖各模块遵守约定。", C.red],
    ];
    notes.forEach(([t, f, d, col], i) => {
      const x = M + i * 4.19;
      T.card(s, x, y0 + 2.46, 4.03, 2.42);
      s.addShape("rect", { x, y: y0 + 2.46, w: 0.07, h: 2.42, fill: { color: col }, line: { color: col, width: 0 } });
      s.addText(t, { x: x + 0.24, y: y0 + 2.56, w: 3.62, h: 0.52, fontFace: F, fontSize: 12,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 15.5 });
      s.addText(f, { x: x + 0.24, y: y0 + 3.10, w: 3.6, h: 0.30, fontFace: F, fontSize: 10.8,
        bold: true, color: C.blue, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: x + 0.24, y: y0 + 3.44, w: 3.60, h: 1.36, fontFace: F, fontSize: 10,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });
    });
    T.foot(s, "实现：mission/fsm.py 十状态　·　suppress.py 三条抑制　·　budget.py 预算与顺延队列");
    s.addNotes("十状态机对应任务书研究内容第 5 项。三条补充说明各自对应任务书的一项要求：复核预算对应巡检效率与证据完整性的取舍，安全事件抢占对应急停避障优先级高于巡检任务。");
  }

  // ============================================================ 系统结构（架构图）
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.系统结构", "3.4　系统架构：四个进程、四条接口与硬件边界",
      ["驱动实例集中在网关一个进程，网关不加载任何模型",
      "放在同一进程里，一次段错误会同时终止感知与控制两侧"]);

    // 横向排布：硬件 1.28 寸，进程 2.30 寸，进程之间留 1.10 寸给接口标注
    const HW = 1.28, PW = 2.30, GUT = 1.10;
    const X = { cam: 0.42, per: 2.12, mis: 5.52, gw: 8.92, act: 11.62 };
    const BY = y0 + 0.06, BH = 2.00;

    function hwBox(x, title, lines) {
      s.addShape("roundRect", { x, y: BY + 0.34, w: HW, h: BH - 0.68, rectRadius: 0.05,
        fill: { color: C.card }, line: { color: C.muted, width: 1, dashType: "dash" } });
      s.addText(title, { x, y: BY + 0.46, w: HW, h: 0.30, align: "center",
        fontFace: F, fontSize: 11, bold: true, color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(lines, { x, y: BY + 0.80, w: HW, h: BH - 1.20, align: "center",
        fontFace: F, fontSize: 9.2, color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 12.5 });
    }
    function procBox(x, name, cn, col, lines, owns) {
      s.addShape("roundRect", { x, y: BY, w: PW, h: BH, rectRadius: 0.06,
        fill: { color: C.white }, line: { color: col, width: 1.75 } });
      s.addShape("rect", { x, y: BY, w: PW, h: 0.42, fill: { color: col }, line: { color: col, width: 0.5 } });
      s.addText(name + "\n" + cn, { x, y: BY, w: PW, h: 0.42, align: "center", valign: "middle",
        fontFace: F, fontSize: 11, bold: true, color: C.white, isTextBox: true, margin: 0 });
      s.addText(lines, { x: x + 0.14, y: BY + 0.52, w: PW - 0.28, h: BH - 1.06,
        fontFace: F, fontSize: 9.8, color: C.text, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });
      s.addText(owns, { x: x + 0.14, y: BY + BH - 0.46, w: PW - 0.28, h: 0.36,
        fontFace: F, fontSize: 9, color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
    }
    /** 进程之间的箭头：箭身居中，编号与报文名在上，传输方式与频率在下，都收在 1.10 寸的通道内 */
    function flow(x, no, msg, spec, col) {
      s.addText(no + "\n" + msg, { x, y: BY + 0.44, w: GUT, h: 0.56, align: "center",
        fontFace: F, fontSize: 9, bold: true, color: col, isTextBox: true, margin: 0, valign: "bottom", lineSpacing: 11.5 });
      s.addShape("rightArrow", { x: x + 0.06, y: BY + 1.04, w: GUT - 0.12, h: 0.34,
        fill: { color: col }, line: { color: col, width: 0.5 } });
      s.addText(spec, { x, y: BY + 1.42, w: GUT, h: 0.50, align: "center",
        fontFace: F, fontSize: 8.5, color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 11 });
    }

    hwBox(X.cam, "相机", "1080p RGB\n光学变焦");
    procBox(X.per, "perception", "感知", C.blue,
      "L1 目标检测 30 Hz\nL2 分割 + 几何读数\nL2′ OCR 铭牌\nL3 非监督未知异常\nL4 规则仲裁 六种结论",
      "绝不碰　驱动实例");
    procBox(X.mis, "mission", "任务", C.navy,
      "十状态复核状态机\n复核预算 N_max\n三条抑制规则\n针孔前馈 + 增益调度 PID",
      "绝不碰　驱动、模型");
    procBox(X.gw, "gateway", "安全网关", C.red,
      "指令白名单 五项校验\n参数硬限，写死在源码\n心跳看门狗 1.5 s 接管\n四个驱动实例（唯一）",
      "绝不碰　模型、图像");
    hwBox(X.act, "底盘 · 云台", "暂停 / 低速\n移动至观察点\n转向 / 变焦\n恢复路线");

    // 相机与执行机构两端的短箭头
    [[X.cam + HW + 0.06, C.muted], [X.gw + PW + 0.06, C.red]].forEach(([x, col]) => {
      s.addShape("rightArrow", { x, y: BY + 1.04, w: 0.30, h: 0.34,
        fill: { color: col }, line: { color: col, width: 0.5 } });
    });
    s.addText("串口", { x: X.gw + PW, y: BY + 1.42, w: 0.42, h: 0.26, align: "center",
      fontFace: F, fontSize: 8.5, color: C.muted, isTextBox: true, margin: 0, valign: "top" });

    flow(X.per + PW, "IF-1", "检出事件", "PUB/SUB\n10 Hz + 按需", C.blue);
    flow(X.mis + PW, "IF-2", "控制指令 / 应答", "REQ/REP\n事件驱动 + 5 Hz 心跳", C.red);

    // IF-3：网关向所有进程回播状态，画成一条自右向左的横箭头
    const SY = BY + BH + 0.14;
    s.addShape("leftArrow", { x: X.per + 0.20, y: SY, w: X.gw + PW - X.per - 0.40, h: 0.32,
      fill: { color: C.greenSoft }, line: { color: C.green, width: 1 } });
    s.addText("IF-3　StatusReport　网关 → 所有进程　20 Hz + 安全事件插播（感知据此判断正处于复核态）", {
      x: X.per + 0.46, y: SY, w: X.gw + PW - X.per - 0.92, h: 0.32, align: "center",
      fontFace: F, fontSize: 9.5, bold: true, color: C.green, isTextBox: true, margin: 0, valign: "middle" });

    // 证据链：感知与任务各写一份到同一目录，上传进程读目录组包上云
    const UY = SY + 0.48, UH = 0.96;
    [X.per + PW / 2, X.mis + PW / 2].forEach(cx => {
      s.addShape("downArrow", { x: cx - 0.13, y: SY + 0.32, w: 0.26, h: 0.16,
        fill: { color: C.amber }, line: { color: C.amber, width: 0.5 } });
    });
    T.card(s, X.per, UY, X.mis + PW - X.per, UH, C.amberSoft);
    s.addText("证据目录　<run_id>/<event_id>/", { x: X.per + 0.20, y: UY + 0.08, w: 3.2, h: 0.30,
      fontFace: F, fontSize: 10.5, bold: true, color: "8A5200", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("第五条接口，但不走总线：before / after 两张图与两份 sidecar 由感知和任务分别写入，上传进程读同一目录组包。", {
      x: X.per + 0.20, y: UY + 0.40, w: 5.30, h: 0.48, fontFace: F, fontSize: 9.2,
      color: "70430A", isTextBox: true, margin: 0, valign: "top", lineSpacing: 12.5 });

    const UP = 8.36, CL = 10.90, CW = 12.90 - CL;
    s.addShape("rightArrow", { x: X.mis + PW + 0.10, y: UY + 0.30, w: 0.34, h: 0.34,
      fill: { color: C.amber }, line: { color: C.amber, width: 0.5 } });
    T.card(s, UP, UY, 2.00, UH);
    s.addText("uploader　上传", { x: UP + 0.14, y: UY + 0.08, w: 1.72, h: 0.30,
      fontFace: F, fontSize: 10.5, bold: true, color: C.navy, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("配对 before/after 组包\n经 HTTP 或 MQTT 上传", { x: UP + 0.14, y: UY + 0.40, w: 1.72, h: 0.48,
      fontFace: F, fontSize: 9.2, color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 12.5 });

    s.addShape("rightArrow", { x: UP + 2.10, y: UY + 0.30, w: 0.34, h: 0.34,
      fill: { color: C.navy }, line: { color: C.navy, width: 0.5 } });
    s.addText("IF-4", { x: UP + 2.02, y: UY + 0.04, w: 0.50, h: 0.24, align: "center",
      fontFace: F, fontSize: 8.5, bold: true, color: C.navy, isTextBox: true, margin: 0, valign: "middle" });
    T.card(s, CL, UY, CW, UH, C.navy);
    s.addText("云端", { x: CL + 0.16, y: UY + 0.08, w: CW - 0.32, h: 0.30, fontFace: F,
      fontSize: 10.5, bold: true, color: "FFD27F", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("台账入库 · 人工复核裁决\n模型版本管理", { x: CL + 0.16, y: UY + 0.40, w: CW - 0.32, h: 0.48,
      fontFace: F, fontSize: 9.2, color: "DCE9F5", isTextBox: true, margin: 0, valign: "top", lineSpacing: 12.5 });

    // 收口：为什么必须拆成四个进程
    const RY = UY + UH + 0.26;
    T.card(s, M, RY, W - M * 2, 0.86, C.blueSoft);
    s.addText("虚线框是硬件，实线框是进程。四个进程只有网关持有驱动实例，" +
      "其余三个即使写错代码也没有可以调用的驱动；网关又不加载任何模型，" +
      "所以感知或任务进程崩溃时它还活着，能把车辆按原路线送完。" +
      "四条接口的报文格式在编码前就冻结成五份 JSON Schema——detection_event、control_command、command_ack、\n" + "status_report、evidence_package，字段全部 additionalProperties: false，配 57 项一致性校验。", {
      x: M + 0.28, y: RY, w: 11.85, h: 0.86, fontFace: F, fontSize: 10.2,
      color: C.navy, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 14 });
    T.foot(s, "接口契约冻结于开题评审；改动走 ALLOWED_DRIFT 白名单，修改字段语义需全组重评审");
    s.addNotes("这一页是系统的全貌。从左往右是一条链：相机采集，感知跑四路模型并由 L4 仲裁，任务决定停不停车、" +
      "怎么对准和变焦，网关校验后才真正驱动底盘与云台。绿色那条自右向左的箭头是网关回播的状态，" +
      "感知靠它判断自己正处于复核态。下面一行是证据链：感知与任务各写一份到同一个证据目录，" +
      "上传进程读目录组包上云，这条不走总线，因为五份 Schema 已经冻结，不容新增字段。" +
      "被问到为什么拆四个进程，答案在最下面那段：只有网关持有驱动实例，而且它不加载模型。");
  }

  // ============================================================ 数据流
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.实施路径", "3.5　这套结构怎么跑起来：一次复核的十二步数据流",
      ["证据目录作为第五条隐式接口，不新增总线报文",
      "五份 Schema 已冻结不容新增字段，而目录结构本身就是契约的一部分"]);
    const rows = [[T.th("#"), T.th("进程"), T.th("动作"), T.th("代码位置")]];
    [["1", "perception", "30 Hz 巡航，L1 检出一块表，置信度落在 0.25 至 0.60 的可疑带内", "node.py::process_frame"],
     ["2", "perception", "铸 event_id（仅在车辆运动且变焦处于广角端时），发 IF-1，is_suspect=true", "node.py，new_uuid()"],
     ["3", "mission", "连续三帧同一 track_id 才确认，再查三条抑制规则与复核预算", "fsm.py::_st_cruise"],
     ["4", "mission", "下发「暂停巡检」，网关执行五项校验后底盘停车", "fsm.py → gateway/checks.py"],
     ["5", "mission", "针孔几何算 aim_offset 做前馈粗对准，再用 PID 闭合像素残差", "fsm.py::_st_aim、servo.py"],
     ["6", "mission", "按 zoom_for_density 算出使成像达标所需的倍率并下发「云台变焦」", "scene/optics.py"],
     ["7", "perception", "从 IF-3 的状态组合识别出正在复核，运行四路模型并做融合", "node.py::verify_due → fusion.py"],
     ["8", "perception", "融合结论与四路各自输出写入 fusion.json", "node.py::_dump_fusion"],
     ["9", "mission", "状态机过程与各状态耗时写入 mission_ctx.json", "node.py::_dump_ctx"],
     ["10", "uploader", "配对 before 与 after，合并两个 sidecar，生成 manifest.json", "packer.py"],
     ["11", "uploader", "先传元数据再传文件，支持断点续传与指数退避", "transport.py"],
     ["12", "cloud", "入库、台账展示、人工复核裁决、模型版本登记", "cloud/db.py、server.py"],
    ].forEach(r => rows.push([
      T.td(r[0], { align: "center", bold: true, color: C.red, fontSize: 10 }),
      T.td(r[1], { fontSize: 10, bold: true, color: C.blue }),
      T.td(r[2], { fontSize: 10 }), T.td(r[3], { fontSize: 9.2, color: C.muted })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [0.42, 1.40, 7.55, 3.12], rowH: 0.295 });

    T.card(s, M, y0 + 4.40, W - M * 2, 0.86, C.blueSoft);
    s.addText("第 8、9 步为什么落文件而不走总线：IF-1 的 Schema 不允许新增字段，OCR 原文与状态机过程放不进去；" +
      "而证据目录的结构本身就是接口契约的一部分，两个进程读写同一个 <run_id>/<event_id>/ 目录属于契约内用法，不必新开第五条接口。", {
      x: M + 0.28, y: y0 + 4.40, w: 11.85, h: 0.86, fontFace: F, fontSize: 10.8,
      color: C.navy, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 15.5 });
    T.foot(s);
    s.addNotes("系统运转的全过程在这一页。十二步跨四个进程与云端，每步都能指到具体文件。第 4 步和第 6 步下发的正是任务书列出的" +
      "高层指令：暂停巡检、云台变焦。");
  }

  // ============================================================ 虚拟试验台
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.研究方法", "3.6　硬件没到怎么验证：虚拟试验台与故障注入",
      ["驱动抽象 + 注入真机故障率的桩，而不是空实现",
      "桩若把目标值直接赋给状态量，任何到位判据都会通过，验证就没有意义"]);
    s.addImage({ path: IMG + "/thirdperson_compare.png", x: M, y: y0, w: 7.30, h: 2.05 });
    s.addText("第三人称机位同时画出车身、云台朝向与当前视锥。变焦 1× 到 3× 时视场角由 60.0° 收窄到 21.8°，" +
      "视锥落在被复核的表盘上。指令下发、云台转动、视场覆盖目标这一串过程因此可以直接观察。", {
      x: M, y: y0 + 2.12, w: 7.30, h: 0.56, fontFace: F, fontSize: 10.2,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14.5 });

    const pts = [
      ["桩不是空实现", "注入真机上实际存在的故障：ACK 丢包 2 %、对焦失败 5 %、云台角速度上限、安全事件 0.05 次/分。若驱动只把目标值直接赋给状态量，任何到位判据都会通过，验证便失去意义。", C.red],
      ["真值与先验严格分开", "世界模型中的 truth 只提供给渲染器与评分逻辑，感知侧读不到。否则精度指标等于用真值核对真值。这条约束是评测结果可信的前提。", C.navy],
      ["串口链路已由假小车验证", "fakecar 以独立进程运行，字节经过内核（POSIX 用 PTY，Windows 用 TCP 环回）。分帧、CRC、超时、重传与丢包注入均按原样发生。", C.green],
      ["硬件到位只改两处", "configs/system.yaml 的 driver_mode 由 stub 改为 real，configs/real.yaml 填端口与限位。上位机代码不动。", C.blue],
    ];
    pts.forEach(([t, d, col], i) => {
      const y = y0 + i * 1.20;
      T.card(s, M + 7.52, y, 4.86, 1.10);
      s.addText(t, { x: M + 7.74, y: y + 0.08, w: 4.4, h: 0.28, fontFace: F, fontSize: 11.5,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 7.74, y: y + 0.36, w: 4.42, h: 0.68, fontFace: F, fontSize: 9.8,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 13.5 });
    });
    const gates = [["接口一致性校验　57 项", "python -m patrol.tools.validate"],
                   ["自动化测试　505 项，覆盖率 75 %", "python -m pytest -q"],
                   ["端到端实跑　300 s 一轮", "python -m patrol.tools.run_all --seconds 300"]];
    gates.forEach(([t, cmd], i) => {
      const y = y0 + 2.86 + i * 0.62;
      T.card(s, M, y, 7.30, 0.54);
      s.addText(t, { x: M + 0.22, y, w: 3.30, h: 0.54, fontFace: F, fontSize: 11.2,
        bold: true, color: C.navy, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(cmd, { x: M + 3.58, y, w: 3.60, h: 0.54, fontFace: "Courier New", fontSize: 9.2,
        color: C.blue, isTextBox: true, margin: 0, valign: "middle" });
    });
    T.foot(s);
    s.addNotes("没有硬件如何开展工作，答案在这一页。桩会注入真机上实际存在的故障，所以在桩上通过的验收有意义。真值与先验严格分开，精" +
      "度数据不是拿真值核对真值得出的。左下三项检查每次提交前都跑。");
  }

  // ============================================================ 资料来源
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "三.资料来源", "3.7　数据从哪来：公开数据训纹理，合成数据补结构",
      ["权重从公开数据集训练，合成数据只做增广",
      "合成图的纹理、光照与背景比不了真实照片；它能补的是密度分层与配对样本"]);
    const rows = [[T.th("来源"), T.th("规模"), T.th("许可"), T.th("用途"), T.th("获取与处理方式")]];
    [["Roboflow distribution_room", "2 773 张\n检测框标注", "CC BY 4.0", "L1 检测训练",
      "注册免费账号导出 YOLOv8 格式；prepare_dataset --to-yolo 统一到本项目三类状态量"],
     ["PaddleX 工业表计读数数据集", "分割 374 训练\n40 验证", "百度官方公开", "L2 分割训练",
      "直链下载无需登录；--from-paddlex 转换，类别映射 pointer→needle、scale→ticks，background 置 255 忽略"],
     ["合成数据集（自建）", "按需生成\n300 至 800 张", "自有", "L1 增广 / L2 掩膜 / L3 正常集",
      "按针孔投影渲染，RGB 与掩膜共用同一套比例常数，逐像素对齐"],
     ["系统运行产出的证据包", "每轮 4 至 6 个", "自有", "L3 正常样本 / 量化校准集",
      "run_all 跑一轮即产出；L3 用 --from-evidence 直接读取，分布最接近运行时"],
    ].forEach(r => rows.push([
      T.td(r[0], { bold: true, fontSize: 10.2, color: C.navy }),
      T.td(r[1], { fontSize: 10, align: "center" }), T.td(r[2], { fontSize: 10, align: "center" }),
      T.td(r[3], { fontSize: 10 }), T.td(r[4], { fontSize: 10, color: C.muted })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [2.35, 1.25, 1.30, 1.95, 5.64], rowH: 0.76 });

    const rules = [
      ["公开数据训纹理，合成数据补结构", C.navy,
       "合成数据在纹理多样性、光照与背景杂物上无法与真实照片相比，因此权重从公开数据集训练。合成数据补的是公开数据在结构上给不了的三样：像素密度分层、同一目标的 before/after 配对、表面文字真值。"],
      ["真实标注用在合成数据最难提供监督的一环", C.green,
       "指针与刻度的区分是合成掩膜最弱的一维。实测仅用合成掩膜训练时指针 IoU 为 0.251，加入 PaddleX 真实标注后升到 0.384，模型与评测口径不变，提高 53 %。"],
      ["识别对象为何是三类状态量", C.red,
       "室内配电室的外观缺陷（渗漏油、呼吸器变色、积水）几乎没有公开标注数据，而状态量（表计、指示灯、开关位置）有配套数据集。外观缺陷改由 L3 非监督异常承接，它只需要正常样本。"],
    ];
    rules.forEach(([t, col, d], i) => {
      const x = M + i * 4.22, y = y0 + 3.96;
      T.card(s, x, y, 4.05, 1.30);
      s.addShape("rect", { x, y, w: 0.07, h: 1.30, fill: { color: col }, line: { color: col, width: 0 } });
      s.addText(t, { x: x + 0.24, y: y + 0.08, w: 3.62, h: 0.34, fontFace: F, fontSize: 11.2,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: x + 0.24, y: y + 0.44, w: 3.62, h: 0.78, fontFace: F, fontSize: 9.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 13 });
    });
    T.foot(s, "任务书未指定数据集，仅点名可用工具：YOLO 系列、MobileNet、EfficientAD 或 PaDiM、ByteTrack、OpenCV");
    s.addNotes("数据从哪来。公开数据集是训练主力，合成数据只补三样结构性的东西。指针 IoU 从 0.251 到 0.384 是实证" +
      "：同一模型只加真实标注就提高 53 %，说明瓶颈在数据，不在模型容量。");
  }
};
