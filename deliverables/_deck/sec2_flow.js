// 二、研究思路和结构
const T = require("./theme");
const { C, F, W, H, M } = T;
const P = require("./newpages");

module.exports = function (pres, IMG) {

  T.divider(pres, "二", "研究思路和结构", "本节回答：思路怎么展开？系统与内容按什么逻辑组织？", ["由三项约束推导出的四个设计决定", "总体方案：主动式复核的十状态流程", "系统结构：四进程、四接口、五份接口契约", "逻辑主线：一次复核的十二步数据流", "研究内容的组织方式：代码分层与模块职责"]);

  // ==================================================== 技术路线：四个设计决定
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究思路：由三项约束推出的四个设计决定",
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

  // ============================================================ 5 主动式复核流程
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "总体方案：主动式复核的十状态流程", "十状态机。每个状态都定义了超时转移，不存在没有出边的状态");
    const steps = [
      ["CRUISE", "30 Hz 巡航\nL1 小模型扫描", C.steel],
      ["SUSPECT", "连续三帧同一目标\n才确认，防抖动", C.steel],
      ["HALT_REQ", "下发 PAUSE\n网关五项校验", C.amber],
      ["AIM", "针孔几何前馈\n+ PID 残差闭环", C.amber],
      ["ZOOM", "按 p_target 算倍率\n变焦到 120 px", C.amber],
      ["VERIFY", "四路模型推理\nL4 显式仲裁", C.green],
      ["PACK", "证据包组装\nbefore/after 配对", C.green],
      ["RESUME", "恢复巡航\n写入抑制表", C.steel],
    ];
    const bw = 1.44, gap = 0.075;
    steps.forEach(([n, d, col], i) => {
      const x = M + i * (bw + gap);
      T.card(s, x, y0 + 0.10, bw, 2.34);
      s.addShape("rect", { x: x, y: y0 + 0.10, w: bw, h: 0.30,
        fill: { color: col }, line: { color: col, width: 0.5 } });
      s.addText(n, { x, y: y0 + 0.10, w: bw, h: 0.30, align: "center", valign: "middle",
        fontFace: F, fontSize: 9.5, bold: true, color: C.white, isTextBox: true, margin: 0 });
      s.addText(d, { x: x + 0.08, y: y0 + 0.48, w: bw - 0.16, h: 1.84, align: "center",
        fontFace: F, fontSize: 10, color: C.text, isTextBox: true, margin: 0,
        valign: "middle", lineSpacing: 15 });
      if (i < steps.length - 1) {
        s.addText("›", { x: x + bw - 0.02, y: y0 + 0.94, w: 0.14, h: 0.5, align: "center",
          fontFace: F, fontSize: 15, bold: true, color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
      }
    });

    const notes = [
      ["复核预算", "N_max = ⌊(T_max − L/v) / T_r⌋", "算得出这一趟还能复核几次，排不下的顺延到下一轮"],
      ["三条抑制", "航点去重 / 定位失效 / 恢复静默", "同一个航点不重复停车，定位丢了就不再触发复核"],
      ["安全优先", "安全事件 200 ms 内中止复核", "任何时刻安全事件都能打断正在进行的复核"],
    ];
    notes.forEach(([t, f, d], i) => {
      const x = M + i * 4.13;
      T.card(s, x, y0 + 2.72, 3.87, 2.12);
      T.cardTitle(s, x + 0.26, y0 + 2.90, 3.4, t, C.amber);
      s.addText(f, { x: x + 0.26, y: y0 + 3.28, w: 3.4, h: 0.32, fontFace: F, fontSize: 11,
        bold: true, color: C.steel, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: x + 0.26, y: y0 + 3.64, w: 3.42, h: 1.10, fontFace: F, fontSize: 11,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 15 });
    });
    T.foot(s, "实现：patrol/mission/fsm.py（598 行，十状态）· suppress.py（三条抑制）· budget.py（预算与顺延队列）");
    s.addNotes("十个状态中这里画出八个主线状态，另外两个是 ABORT 和 ERROR。设计上有一条约束：每个状态都必须定义超时转移，因此状态机不会停在某个状态上不动。复核预算公式的作用是限制单轮巡检中复核的总次数，使主动复核不会超出 30 min 的巡检时间预算。");
  }

  // ============================================================ 6 架构
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "系统结构：四进程、四接口与五份接口契约", "进程边界按安全职责划分：安全网关必须在感知与任务进程异常退出后继续工作");
    const procs = [
      ["perception  感知", "相机、四路模型", "驱动以 passive 建，收不到指令", C.steel],
      ["mission  任务", "十状态机、PID、预算", "不碰驱动，不碰模型", C.steel],
      ["gateway  安全网关", "四个驱动实例（唯一）", "不碰模型、不碰图像", C.red],
      ["uploader  上传", "证据目录、上传队列", "不碰驱动，不碰模型", C.steel],
    ];
    procs.forEach(([n, own, never, col], i) => {
      const x = M + i * 3.10;
      T.card(s, x, y0, 2.90, 2.20);
      s.addText(n, { x: x + 0.22, y: y0 + 0.18, w: 2.5, h: 0.36, fontFace: F, fontSize: 13.5,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText("拥有  " + own, { x: x + 0.22, y: y0 + 0.62, w: 2.5, h: 0.52, fontFace: F,
        fontSize: 10.5, color: C.text, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });
      s.addText("绝不碰  " + never, { x: x + 0.22, y: y0 + 1.16, w: 2.5, h: 0.56, fontFace: F,
        fontSize: 10.5, color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });
    });

    const ifs = [
      ["IF-1", "DetectionEvent", "感知 → 任务 / 上传", "PUB/SUB", "10 Hz + 按需"],
      ["IF-2", "ControlCommand / Ack", "任务 → 网关", "REQ/REP", "事件驱动 + 5 Hz 心跳"],
      ["IF-3", "StatusReport", "网关 → 所有人", "PUB/SUB", "20 Hz + 安全插播"],
      ["IF-4", "EvidencePackage", "上传 → 云端", "HTTP / MQTT", "每次复核一包"],
    ];
    const rows = [[T.th("编号"), T.th("报文"), T.th("方向"), T.th("传输"), T.th("频率")]];
    ifs.forEach(r => rows.push([
      T.td(r[0], { bold: true, color: C.amber, align: "center" }),
      T.td(r[1], { bold: true }), T.td(r[2]), T.td(r[3], { align: "center" }), T.td(r[4])]));
    T.table(s, rows, { x: M, y: y0 + 2.44, w: 7.55, colW: [0.78, 2.12, 1.85, 1.10, 1.70], rowH: 0.40 });

    T.card(s, M + 7.85, y0 + 2.44, 4.24, 2.66, C.ink);
    s.addText("为什么非要拆四个进程", { x: M + 8.10, y: y0 + 2.66, w: 3.8, h: 0.34, fontFace: F,
      fontSize: 13.5, bold: true, color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("安全网关必须是执行器的唯一入口，并且要在感知或任务进程异常退出后继续工作。" +
      "放在同一进程内无法做到：一次段错误会同时终止两侧。\n\n" +
      "「终止感知与任务进程后，车辆仍按路线走完」这条演示验证的就是这个边界。", {
      x: M + 8.10, y: y0 + 3.10, w: 3.78, h: 1.86, fontFace: F, fontSize: 11,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 16 });
    T.foot(s, "五份 JSON Schema 全部 additionalProperties: false；改接口要走 validate.py 的 ALLOWED_DRIFT 白名单流程");
    s.addNotes("本页回答架构复杂度的问题。四个进程的边界按安全职责划分，不按代码量划分。五份 Schema 是冻结的接口契约，修改任何一个字段都要走 ALLOWED_DRIFT 白名单流程，因此四个人可以并行修改各自的模块而不产生接口冲突。");
  }

  // ==================================================== 一次复核的完整数据流
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "逻辑主线：一次复核的完整数据流",
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

  // ==================================================== 代码结构与模块职责
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "研究内容的组织：代码分层与模块职责",
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
