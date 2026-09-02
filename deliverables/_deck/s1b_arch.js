// 架构详解：技术路线、一次复核的数据流、接口冻结机制、代码分层
const T = require("./theme");
const { C, F, W, H, M } = T;

module.exports = function (pres, IMG) {

  // ==================================================== 技术路线：四个设计决定
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "技术路线：从三项要求推出的四个设计决定",
      "每个决定对应上一页的一项约束，可以逐条追溯到任务书要求");
    const D = [
      ["01", "两段式感知", "巡航期与复核期用不同的模型和不同的输入分辨率",
       "精度要求 0.5 % FS 对应 120 px 的成像下限，而 5 m 处 1× 只有 50 px。" +
       "全程按复核精度采集会超出 30 min 的时间预算，所以把采集分成两段：" +
       "巡航期只做检出，复核期才做测量。", C.amber],
      ["02", "执行器单一出口", "感知与任务进程都不持有驱动实例，指令一律经安全网关下发",
       "模型存在误判，而误判会转化为车辆动作。把对执行器的访问集中到唯一进程，" +
       "并让该进程不加载任何模型，是为了让它能在感知或任务进程异常退出后继续工作。", C.red],
      ["03", "接口先冻结再实现", "五份 JSON Schema 在编码前定稿，字段全部 additionalProperties: false",
       "四个人要并行开发，进程之间又必须能对接。先把报文结构固定下来，" +
       "各自实现时就不会互相等待，联调时也不会出现字段对不上的情况。", C.steel],
      ["04", "驱动抽象与故障注入", "四个抽象基类隔离硬件，桩注入真机上实际存在的故障率",
       "硬件到位时间不确定，但控制与状态机逻辑不应该等硬件。" +
       "桩按真实角速度约束运动，并注入 2 % ACK 丢包与 5 % 对焦失败，" +
       "使在桩上通过的验收具有参考价值。", C.green],
    ];
    D.forEach(([n, t, k, d, col], i) => {
      const y = y0 + i * 1.30;
      T.card(s, M, y, W - M * 2, 1.20);
      s.addText(n, { x: M + 0.28, y, w: 0.62, h: 1.20, fontFace: F, fontSize: 22,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(t, { x: M + 1.00, y: y + 0.12, w: 2.55, h: 0.42, fontFace: F, fontSize: 14.5,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(k, { x: M + 1.00, y: y + 0.56, w: 2.60, h: 0.56, fontFace: F, fontSize: 9.8,
        color: col, isTextBox: true, margin: 0, valign: "top", lineSpacing: 13 });
      s.addText(d, { x: M + 3.78, y: y + 0.14, w: 8.05, h: 0.96, fontFace: F, fontSize: 11,
        color: C.muted, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 16 });
    });
    T.foot(s, "四个决定共同构成「代码先行、模型后训、硬件后接」这条实施路径");
    s.addNotes("本页把技术选型与任务书要求对应起来。评审如果问某个设计的依据，答案在右侧一列。这四条决定互相支撑：接口先冻结，四个人才能并行开发；有驱动抽象，控制逻辑才能不等硬件；采用两段式采集，精度与时间预算才能同时满足。");
  }

  // ==================================================== 一次复核的完整数据流
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "一次复核的完整数据流",
      "十二步，跨四个边缘进程与云端。这条链路能完整走通，系统各层的接口就是对的");
    const rows = [[T.th("#"), T.th("进程"), T.th("动作"), T.th("代码位置")]];
    const steps = [
      ["1", "perception", "30 Hz 巡航，L1 检出一块表，置信度落在 0.25 至 0.60 的可疑带内", "node.py::process_frame"],
      ["2", "perception", "铸 event_id（仅在车辆运动且变焦处于广角端时铸），发 IF-1，is_suspect=true", "node.py，new_uuid()"],
      ["3", "mission", "连续三帧同一 track_id 才确认，再查三条抑制规则与复核预算", "fsm.py::_st_cruise"],
      ["4", "mission", "下发 PAUSE，网关执行五项校验后底盘停车", "fsm.py::_st_halt_req → gateway/checks.py"],
      ["5", "mission", "针孔几何算 aim_offset 做前馈粗对准，再用 PID 闭合像素残差", "fsm.py::_st_aim、servo.py"],
      ["6", "mission", "按 zoom_for_density(1.0, p_before, 120, 3.0) 算倍率并下发", "scene/optics.py"],
      ["7", "perception", "从 IF-3 的状态组合识别出正在复核，运行四路模型并做融合", "node.py::verify_due → fusion.py"],
      ["8", "perception", "融合结论写入 evidence/<run>/<event>/fusion.json", "node.py::_dump_fusion"],
      ["9", "mission", "状态机过程写入 mission_ctx.json", "node.py::_dump_ctx"],
      ["10", "uploader", "配对 before 与 after，合并两个 sidecar，生成 manifest.json", "node.py::_finish、packer.py"],
      ["11", "uploader", "先传元数据再传文件，支持断点续传与指数退避", "transport.py"],
      ["12", "cloud", "入库、台账展示、人工裁决", "cloud/db.py、server.py"],
    ];
    steps.forEach(r => rows.push([
      T.td(r[0], { align: "center", bold: true, color: C.amber, fontSize: 9.5 }),
      T.td(r[1], { fontSize: 9.5, bold: true, color: C.steel }),
      T.td(r[2], { fontSize: 9.5 }),
      T.td(r[3], { fontSize: 9, color: C.muted })]));
    T.table(s, rows, { x: M, y: y0, w: W - M * 2, colW: [0.45, 1.42, 7.30, 2.92], rowH: 0.283 });

    T.card(s, M, y0 + 3.80, W - M * 2, 0.94, C.ink);
    s.addText("第 8 步与第 9 步为什么走文件而不走总线", { x: M + 0.34, y: y0 + 3.90, w: 11.4, h: 0.30,
      fontFace: F, fontSize: 12.5, bold: true, color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("IF-1 的 Schema 是 additionalProperties: false，OCR 原文与状态机内部过程放不进去。" +
      "而 ICD §6.1 本身就把证据目录的结构定义为契约，两个进程读写同一个 <run_id>/<event_id>/ 目录" +
      "属于该契约内的用法，不需要新开第五条接口。", {
      x: M + 0.34, y: y0 + 4.22, w: 11.4, h: 0.48, fontFace: F, fontSize: 10.5,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });
    T.foot(s, "读代码建议顺这条线读一遍；每一步都有对应的测试用例");
    s.addNotes("本页说明系统的运行过程。十二步跨四个进程与云端，每一步都能对应到具体文件。第 8、9 步走文件而不走总线，是因为 Schema 冻结后不允许新增字段，而证据目录本身就是 ICD 定义的契约。");
  }

  // ==================================================== 接口冻结机制
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "接口怎么冻结：五份 Schema 与改动成本表",
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

  // ==================================================== 代码结构与模块职责
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "代码结构：每个模块负责什么",
      "按职责分层。同一层内的模块可以互换实现，跨层调用不允许绕过既定接口");
    const layers = [
      ["cloud/", "云端", "500 行", C.steel,
       "FastAPI 接收证据包，SQLite 存台账。五个页签：台账、人工复核、复核增益、模型版本、实时遥测。实时那一路只在内存保留最近一段，不落库。"],
      ["uploader/", "证据与上传", "918 行", C.steel,
       "配对 before 与 after，合并两个进程写出的 sidecar，生成 manifest。上传队列支持断点续传与指数退避。磁盘保留策略是全仓库唯一会删文件的地方，未确认上传的证据永不自动删除。"],
      ["mission/", "任务决策", "1 309 行", C.amber,
       "十状态机、云台 PID 与增益调度、三条抑制规则、复核预算与顺延队列。不持有驱动实例，也不加载模型，只发指令。"],
      ["gateway/", "安全网关", "697 行", C.red,
       "指令白名单、五项校验、参数范围硬编码、心跳看门狗、审计日志。持有全部四个驱动实例，是执行器的唯一入口。"],
      ["perception/", "识别", "3 665 行", C.green,
       "四路模型加融合仲裁：L1 检测、L2 分割与几何读数、L2′ OCR 互证、L3 非监督异常、L4 显式仲裁。相机以 passive 方式建立，收不到控制指令。"],
      ["drivers/  scene/", "硬件抽象与虚拟场景", "3 408 行", C.muted,
       "四个抽象基类隔离硬件，factory.py 是全仓库唯一的 driver_mode 分支点。scene 提供针孔光学、世界模型与表计绘制，只在 stub 模式加载。"],
    ];
    layers.forEach(([n, role, loc, col, d], i) => {
      const y = y0 + i * 0.80;
      T.card(s, M, y, W - M * 2, 0.72);
      s.addText(n, { x: M + 0.26, y, w: 1.70, h: 0.72, fontFace: "Courier New", fontSize: 11,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(role, { x: M + 1.98, y, w: 1.62, h: 0.72, fontFace: F, fontSize: 10.5,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(loc, { x: M + 3.60, y, w: 0.92, h: 0.72, align: "right", fontFace: F, fontSize: 10.5,
        bold: true, color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 4.72, y: y + 0.03, w: 7.16, h: 0.66, fontFace: F, fontSize: 9.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "middle", lineSpacing: 12.5 });
    });
    T.card(s, M, y0 + 4.92, W - M * 2, 0.56, C.amberSoft);
    s.addText("另有 common/ 869 行（双时间戳、报文构造与 Schema 校验、ZeroMQ 封装）、" +
      "tools/ 2 534 行（校验、预览、标定、PID 整定、假小车、模型横向对比）、training/ 1 418 行（数据集接入与三类训练、ONNX 与 RKNN 导出）", {
      x: M + 0.30, y: y0 + 4.92, w: 11.5, h: 0.56, fontFace: F, fontSize: 10,
      color: "8A5A05", isTextBox: true, margin: 0, valign: "middle", lineSpacing: 13 });
    T.foot(s, "分层原则：同层可换实现，跨层不绕过接口。感知进程取不到执行器，网关进程不加载模型");
    s.addNotes("本页说明代码的组织方式。分层原则有两条：同一层内可以更换实现，例如检测器由合成检测器换成 YOLO，上层代码不需要修改；跨层调用不绕过既定接口，感知进程取不到执行器，网关进程不加载模型。");
  }
};
