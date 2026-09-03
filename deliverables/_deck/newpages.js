// 五段式结构补充的页：现状分析、研究目标与结果、资料来源、创新点、参考文献
const T = require("./theme");
const { C, F, W, H, M } = T;

const P = {};

// ---------------------------------------------------------------- 现状分析
P.status = function (pres, IMG) {
  const s = T.slide(pres);
  const y0 = T.head(s, "auto", "国内外现状：三类既有方案与它们的边界",
    "配电室巡检已有成熟产品，本课题要解决的是它们共同回避的那一段");
  const rows = [[T.th("方案类别"), T.th("代表做法"), T.th("能解决的"), T.th("回避的问题")]];
  [["固定式在线监测", "在每台设备旁装传感器与摄像头，接入综合监控系统",
    "连续监测、响应快、无运动部件", "改造成本随设备数线性增长；老站点布线困难；视角固定，装错位置就读不到"],
   ["轨道式巡检机器人", "沿预埋导轨往复运行，定点拍摄并回传",
    "路径确定、定位精度高、供电稳定", "轨道属土建改造；路径不可变更；仍以录像回传为主，读数依赖后端人工或离线算法"],
   ["轮式巡检机器人（本课题）", "自主导航至巡检位，云台对准后采集",
    "无需土建改造、路径可重规划", "定位与云台误差直接影响成像；算力受限，无法全程按高精度采集"],
  ].forEach(r => rows.push([
    T.td(r[0], { bold: true, fontSize: 10.5, color: C.steel }),
    T.td(r[1], { fontSize: 10 }), T.td(r[2], { fontSize: 10, color: C.green }),
    T.td(r[3], { fontSize: 10, color: C.muted })]));
  T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [2.15, 3.05, 2.65, 4.24], rowH: 0.86 });

  T.card(s, M, y0 + 2.86, W - M * 2, 1.90, C.ink);
  s.addText("三类方案的共同缺口：把「采集」和「判读」当成两件事", {
    x: M + 0.36, y: y0 + 3.00, w: 11.4, h: 0.36, fontFace: F, fontSize: 15, bold: true,
    color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
  s.addText("现有方案普遍按固定参数采集，再把图像交给后端判读。这条路线的问题在于：" +
    "采集时不知道这一帧够不够用，判读时已经无法补救。表计成像不足 120 px 时，" +
    "后端再强的算法也读不出 0.5 % FS 的精度，因为信息在光学环节就已经丢失。\n\n" +
    "本课题把判读结果反馈回采集环节：先低成本扫描，检出可疑目标后停车、对准、变焦、重新采集，" +
    "使采集参数由目标本身决定。这是「主动式」三个字的实际含义，也是本课题与上述三类方案的区别所在。", {
    x: M + 0.36, y: y0 + 3.42, w: 11.4, h: 1.24, fontFace: F, fontSize: 11.5,
    color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 17 });
  T.foot(s, "现状调研见设计方案书 §1.2 与 §6.2.1；识别对象的选取依据见后文「资料来源」一页");
  s.addNotes("本页交代国内外现状。三类方案各有适用场景，本课题不是要取代它们，而是补上一个共同缺口：现有方案按固定参数采集，采集时不知道这一帧够不够用。我们把判读结果反馈回采集环节，让采集参数由目标本身决定。");
};

// ------------------------------------------------------ 研究目标与主要结果
P.goals = function (pres, IMG) {
  const s = T.slide(pres);
  const y0 = T.head(s, "auto", "研究目标与主要结果",
    "四项目标对应任务书要求，右列为本阶段已取得的实测结果");
  const rows = [[T.th("研究目标"), T.th("量化要求"), T.th("本阶段结果"), T.th("状态")]];
  const data = [
    ["建立可主动调整采集参数的巡检系统", "复核后成像放大 ≥ 1.5 倍", "像素密度比 1.9 至 2.3", "达成"],
    ["表计读数达到工业测量精度", "基本误差 ≤ 0.5 % FS\n线性度 ≤ 0.4 % FS", "0.469 % FS\n0.267 % FS", "达成"],
    ["", "重复性 ≤ 0.3 % FS", "0.321 % FS", "未达成"],
    ["云台伺服满足复核时的对准要求", "超调 ≤ 10 %，调节时间 ≤ 1.5 s", "3× 变焦：超调 1.0 %，1.202 s", "达成"],
    ["建立不依赖缺陷标注的异常发现能力", "对训练集外的异常可检出", "漏报 3.3 %，误报 3.8 %", "达成"],
    ["保证 AI 误判不转化为车辆动作", "三层边界可现场验证", "三条演示均可当场执行", "达成"],
  ];
  data.forEach(r => rows.push([
    T.td(r[0], { bold: true, fontSize: 10 }),
    T.td(r[1], { fontSize: 10, color: C.steel }),
    T.td(r[2], { fontSize: 10, bold: true, color: r[3] === "达成" ? C.green : C.red }),
    T.td(r[3], { align: "center", bold: true, fontSize: 10,
      color: r[3] === "达成" ? C.green : C.red,
      fill: { color: r[3] === "达成" ? C.greenSoft : C.redSoft } })]));
  T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [3.62, 2.85, 3.42, 2.20], rowH: 0.55 });

  const st = [["501", "项自动化测试通过", C.green], ["51", "项接口一致性校验", C.green],
              ["4", "路模型协同已接通", C.steel], ["1", "项指标压线未达标", C.red]];
  st.forEach(([v, k, col], i) => {
    const x = M + i * 3.10;
    T.card(s, x, y0 + 4.05, 2.90, 1.24);
    T.stat(s, x + 0.26, y0 + 4.21, 2.45, v, k, col);
  });
  T.foot(s, "指标出处：设计方案书 §2.2 表 2-2；未达成项已列入不足与下一阶段计划");
  s.addNotes("本页把研究目标与实测结果并排列出。六条量化要求中五条达成，重复性 0.321 % FS 超出 0.3 % 的限值，这一条主动写在这里，不放到最后。评审通常会先看这张表，然后挑一两项追问过程，后面的研究内容部分就是逐项展开。");
};

// ---------------------------------------------------------------- 资料来源
P.data = function (pres, IMG) {
  const s = T.slide(pres);
  const y0 = T.head(s, "auto", "资料来源：数据从何获取，如何处理",
    "公开数据集为主，合成数据只补公开数据在结构上给不了的部分");
  const rows = [[T.th("来源"), T.th("规模"), T.th("许可"), T.th("用途"), T.th("获取与处理方式")]];
  [["Roboflow distribution_room", "2 773 张\n检测框标注", "CC BY 4.0", "L1 检测训练",
    "注册免费账号后导出 YOLOv8 格式；prepare_dataset --to-yolo 统一到本项目三类状态量"],
   ["PaddleX 工业表计读数数据集", "分割 374 训练\n40 验证", "百度官方公开", "L2 分割训练",
    "直链 wget，无需登录；--from-paddlex 转换，类别映射 pointer→needle、scale→ticks，background 映射为 255 忽略"],
   ["合成数据集（自建）", "按需生成\n300 至 800 张", "自有", "L1 增广 / L2 掩膜 / L3 正常集",
    "gen_synthetic 按针孔投影渲染，RGB 与掩膜共用同一套比例常数，逐像素对齐"],
   ["系统运行产出的证据包", "每轮 4 至 6 个", "自有", "L3 正常样本 / 量化校准集",
    "run_all 跑一轮即产出；L3 用 --from-evidence 直接读取，分布最接近运行时"],
  ].forEach(r => rows.push([
    T.td(r[0], { bold: true, fontSize: 9.5, color: C.steel }),
    T.td(r[1], { fontSize: 9.5, align: "center" }),
    T.td(r[2], { fontSize: 9.5, align: "center" }),
    T.td(r[3], { fontSize: 9.5 }),
    T.td(r[4], { fontSize: 9.5, color: C.muted })]));
  T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [2.42, 1.28, 1.35, 1.95, 5.09], rowH: 0.86 });

  const rules = [
    ["公开数据训纹理，合成数据补结构", C.amber,
     "合成数据在纹理多样性、光照、背景杂物上无法与真实照片相比，因此权重从公开数据集训练。" +
     "合成数据补的是公开数据在结构上给不了的三样：像素密度分层、同一目标的 before/after 配对、表面文字的真值。"],
    ["真实标注用在合成数据最教不会的一环", C.green,
     "指针与刻度的区分是合成掩膜最弱的一维，实测仅用合成掩膜训练时指针 IoU 只有 0.251。" +
     "加入 PaddleX 真实标注后升到 0.384，同一模型同一评测口径，提高 53 %。"],
  ];
  rules.forEach(([t, col, d], i) => {
    const y = y0 + 3.72 + i * 0.92;
    T.card(s, M, y, W - M * 2, 0.84);
    s.addText(t, { x: M + 0.28, y, w: 3.35, h: 0.84, fontFace: F, fontSize: 11.5,
      bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
    s.addText(d, { x: M + 3.72, y: y + 0.06, w: 8.15, h: 0.72, fontFace: F, fontSize: 9.8,
      color: C.muted, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 13 });
  });
  T.foot(s, "两份公开数据集的选取依据见 training/README.md；转换脚本与类别映射见 training/prepare_dataset.py");
  s.addNotes("本页回答资料从何获取。要强调分工：公开数据集是训练主力，合成数据只补三样公开数据在结构上给不了的东西。指针 IoU 从 0.251 到 0.384 这一步是实证：同一模型只加真实标注就提高 53 %，说明瓶颈在数据而不在模型容量。");
};

// ---------------------------------------------------------------- 创新点
P.novelty = function (pres, IMG) {
  const s = T.slide(pres);
  const y0 = T.head(s, "auto", "主要创新点",
    "四条，每条都给出与既有做法的区别以及本阶段的实测支撑");
  const N = [
    ["01", "采集参数由判读结果决定的主动式复核", C.amber,
     "既有巡检系统按固定参数采集，采集时无法判断这一帧是否够用。本系统把判读结果反馈回采集环节：" +
     "检出可疑目标后停车、对准，并按 zoom_for_density 算出使成像达到 120 px 所需的变焦倍率。",
     "实测像素密度由 50.0 px 提高到 150.0 px，与针孔投影公式的计算值相差 0.4 %"],
    ["02", "变焦增益调度的云台伺服", C.red,
     "画面位移与变焦倍率成正比，固定增益的 PID 在变焦后等效增益被放大。本系统按 ω = θ/(W·z)·u 缩放控制量，" +
     "使同一组参数在 1× 与 3× 下都稳定。",
     "3× 变焦超调 1.0 %、调节时间 1.202 s；关闭调度后超调升至 37.7 %"],
    ["03", "四类模型协同并由显式规则仲裁", C.steel,
     "检出、测量、认字、发现未知异常这四个子问题对速度、精度、输出形式的要求互相冲突，单一模型无法兼顾。" +
     "本系统按四路分别实现，再由纯规则的 L4 层仲裁，每条结论附带 reasons 字段可追溯。",
     "六种结论各有测试用例；L3 对训练集外异常的漏报由 90.8 % 降至 3.3 %"],
    ["04", "接口先冻结、驱动先抽象的无硬件开发方法", C.green,
     "五份 JSON Schema 在编码前定稿并配 51 项一致性校验；四个抽象基类隔离硬件，" +
     "桩注入真机上实际存在的故障率。硬件到位时只需修改两处配置。",
     "501 项测试、端到端 300 s 闭环均在无硬件条件下完成；假小车已验证串口协议栈"],
  ];
  N.forEach(([n, t, col, d, ev], i) => {
    const y = y0 + i * 1.28;
    T.card(s, M, y, W - M * 2, 1.18);
    s.addText(n, { x: M + 0.26, y, w: 0.60, h: 1.18, fontFace: F, fontSize: 21,
      bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
    s.addText(t, { x: M + 0.94, y: y + 0.10, w: 5.10, h: 0.38, fontFace: F, fontSize: 13.5,
      bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
    s.addText(d, { x: M + 0.94, y: y + 0.48, w: 5.15, h: 0.64, fontFace: F, fontSize: 9.3,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 12 });
    T.card(s, M + 6.28, y + 0.14, 5.81, 0.90, C.card);
    s.addText("实测支撑", { x: M + 6.52, y: y + 0.20, w: 5.3, h: 0.26, fontFace: F, fontSize: 9.5,
      bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
    s.addText(ev, { x: M + 6.52, y: y + 0.46, w: 5.33, h: 0.52, fontFace: F, fontSize: 10,
      color: C.text, isTextBox: true, margin: 0, valign: "top", lineSpacing: 13 });
  });
  T.foot(s, "四条创新点均可在本机复现，对应命令见各研究内容页的页脚");
  s.addNotes("这一页是答辩的重点。四条创新点里第一条是本课题的立论，第二条是控制部分的贡献，第三条是识别部分的贡献，第四条是工程方法上的贡献。每条右侧都给了实测支撑，不是只有说法。");
};

// ---------------------------------------------------------------- 参考文献
P.refs = function (pres, IMG) {
  const s = T.slide(pres);
  const y0 = T.head(s, "auto", "参考资料与处理方式",
    "分为规范文件、公开数据集、方法来源三类，全部在仓库内可追溯");
  const rows = [[T.th("类别"), T.th("名称"), T.th("在本项目中的作用"), T.th("处理方式")]];
  [["规范文件", "《测控系统综合实训课题任务书》", "三项验收指标的来源", "指标逐条对应到 configs 与测试用例"],
   ["规范文件", "《配电室设备状态视觉测控系统 设计方案书》", "识别对象选取、精度指标、交付物清单", "与 ICD 逐条比对，出具 22 处差异清单"],
   ["规范文件", "《ICD-RK3576-PATROL v1.0》接口控制文件", "四条接口与五份 Schema 的定义", "编码前冻结，改动走 ALLOWED_DRIFT 白名单"],
   ["公开数据集", "Roboflow distribution_room（CC BY 4.0）", "L1 检测训练集，2 773 张", "转 YOLO 格式，类别映射到本项目三类"],
   ["公开数据集", "PaddleX 工业表计读数数据集（百度公开）", "L2 分割训练集，414 张像素级标注", "类别映射 + background 置 255 忽略"],
   ["方法来源", "PaDiM：基于分块分布建模的异常检测", "L3 采用方案", "复现全协方差版本，与对角版本、统计法同批比选"],
   ["方法来源", "EfficientAD：轻量化蒸馏异常检测", "L3 候选方案", "实测分数倒挂，未采用，原因与数据一并记录"],
   ["方法来源", "YOLO11（Ultralytics）", "L1 检测骨干", "巡航级 yolo11s 已训，复核级 yolo11m 待训"],
  ].forEach(r => rows.push([
    T.td(r[0], { bold: true, fontSize: 9.5, align: "center", color: C.steel }),
    T.td(r[1], { fontSize: 9.5 }), T.td(r[2], { fontSize: 9.5 }),
    T.td(r[3], { fontSize: 9.5, color: C.muted })]));
  T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [1.32, 3.72, 3.05, 4.00], rowH: 0.44 });

  T.card(s, M, y0 + 3.72, W - M * 2, 1.02, C.ink);
  s.addText("参考资料的处理原则：引用之后必须落到可执行的检查上", {
    x: M + 0.36, y: y0 + 3.84, w: 11.4, h: 0.32, fontFace: F, fontSize: 13, bold: true,
    color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
  s.addText("规范文件的每一项指标都对应到 configs 中的一个常量和至少一条测试用例，" +
    "方案书与 ICD 的不一致之处逐条列入差异清单并在评审中裁定；" +
    "公开数据集的类别与本项目不一致时，映射规则写进 prepare_dataset.py 并配核对图，不做隐式转换；" +
    "方法来源按同一批样本、同一阈值复现后再比选，未采用的方案连同失败原因一并记录。", {
    x: M + 0.36, y: y0 + 4.18, w: 11.4, h: 0.50, fontFace: F, fontSize: 10.5,
    color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });
  T.foot(s, "完整差异清单见 docs/一致性差异清单-方案书-ICD-v1.0.md（22 处，分 A/B/C/D 四类）");
  s.addNotes("本页交代参考了什么、怎么处理的。要强调最后那条原则：引用之后必须落到可执行的检查上。指标对应到常量和测试用例，数据集映射写进脚本并配核对图，方法来源同批复现后再比选。未采用的 EfficientAD 也连同失败原因记录在案。");
};

module.exports = P;
