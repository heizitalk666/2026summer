// 第 1–9 页：封面、目录、课题、核心立论、复核流程、架构、无硬件方案、工作量、质量
const T = require("./theme");
const { C, F, W, H, M } = T;

module.exports = function (pres, IMG) {
  // ============================================================ 1 封面
  {
    const s = T.slide(pres);
    T.darkBg(s);
    // 母题：右侧一组同心圆环（表盘）
    s.addShape("ellipse", { x: 9.05, y: 1.15, w: 3.95, h: 3.95,
      fill: { color: C.ink }, line: { color: C.amber, width: 1.5 } });
    s.addShape("ellipse", { x: 9.52, y: 1.62, w: 3.01, h: 3.01,
      fill: { color: C.ink }, line: { color: C.steel, width: 1 } });
    // 指针：以轴心为中心画（穿过轴心的针 + 配重尾，正是本项目表计的样子）；
    // pptxgenjs 绕形状中心旋转，所以中心必须和轴心重合，否则针会「飘」在轴心外。
    s.addShape("rect", { x: 11.0025, y: 2.525, w: 0.045, h: 1.20,
      fill: { color: C.amber }, line: { color: C.amber, width: 0.5 }, rotate: 34 });
    s.addShape("ellipse", { x: 10.855, y: 2.955, w: 0.34, h: 0.34,
      fill: { color: C.amber }, line: { color: C.amber, width: 1 } });

    s.addText("中期答辩", {
      x: M, y: 1.30, w: 7.6, h: 0.4, fontFace: F, fontSize: 15, bold: true,
      color: C.amber, charSpacing: 3, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("基于 RK3576 边缘计算的\n无人车主动式 AI 巡检系统", {
      x: M, y: 1.85, w: 8.0, h: 1.85, fontFace: F, fontSize: 37, bold: true,
      color: C.white, isTextBox: true, margin: 0, valign: "top", lineSpacing: 48 });
    s.addText("配电室设备状态视觉测控 · 停车对准变焦，把表盘从 50 px 放大到 150 px 再读", {
      x: M, y: 3.86, w: 8.0, h: 0.42, fontFace: F, fontSize: 14,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "middle" });

    const chips = [["21 100", "行代码"], ["501", "项测试"], ["4", "路模型"], ["51", "项接口校验"]];
    chips.forEach(([v, k], i) => {
      const x = M + i * 1.98;
      s.addText(v, { x, y: 4.62, w: 1.8, h: 0.5, fontFace: F, fontSize: 25, bold: true,
        color: C.amber, isTextBox: true, margin: 0, valign: "bottom" });
      s.addText(k, { x, y: 5.14, w: 1.8, h: 0.3, fontFace: F, fontSize: 11,
        color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top" });
    });

    s.addText("四人小组 · 甲 L1 检测 / 乙 L2 分割 / 丙 L3 异常 / 组长 系统与交付        2026 年 9 月", {
      x: M, y: 6.55, w: W - M * 2, h: 0.34, fontFace: F, fontSize: 11,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "middle" });
    s.addNotes("开场：本系统的做法是，巡航时以低算力扫描一遍，检出可疑目标后停车、对准、变焦、重新拍摄。这一步把表盘成像从 50 像素放大到 150 像素，读数精度才能达到 0.5 % FS 的要求。与「沿途录像、回去离线分析」的区别就在这里。硬件尚未到位，但整套系统可以在笔记本上完整运行，所有指标均为实测值。");
  }

  // ============================================================ 2 目录
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "目", "汇报提纲", "五个部分。第二部分讲架构，第四部分是三条识别路线的实测数据");
    const secs = [
      ["01", "课题与核心立论", "三项要求为何互相冲突，像素密度与读数精度的关系，主动复核的流程", "3 – 5"],
      ["02", "系统架构与实现", "四进程四接口、一次复核的十二步数据流、接口冻结机制、代码分层", "6 – 11"],
      ["03", "工作量、质量与完成度", "代码与测试规模、三项检查、逐项列出的完成度总表", "12 – 14"],
      ["04", "三条识别路线的实测成果", "L1 检测、L2 分割、L3 异常各自的数据与比选结论", "15 – 28"],
      ["05", "系统能力与收口", "安全边界、云台控制、端到端闭环、风险与下一步", "29 – 35"],
    ];
    secs.forEach(([n, t, d, p], i) => {
      const y = y0 + 0.04 + i * 1.11;
      T.card(s, M, y, W - M * 2, 0.96);
      s.addText(n, { x: M + 0.3, y, w: 0.9, h: 0.96, fontFace: F, fontSize: 26, bold: true,
        color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(t, { x: M + 1.24, y: y + 0.12, w: 8.6, h: 0.36, fontFace: F, fontSize: 16,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 1.24, y: y + 0.50, w: 9.4, h: 0.34, fontFace: F, fontSize: 11,
        color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
      s.addText("P " + p, { x: W - M - 1.5, y, w: 1.2, h: 0.96, align: "right",
        fontFace: F, fontSize: 12, color: C.steel, isTextBox: true, margin: 0, valign: "middle" });
    });
    T.foot(s, null);
    s.addNotes("提纲四部分。时间紧的话，重点讲 01 的像素密度立论和 03 的三条实测结论。");
  }

  // ============================================================ 3 课题与难点
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "课题：配电室要什么样的巡检", "任务书三项要求，每一项都指向同一个矛盾");
    const items = [
      ["看得清", "表计读数误差 ≤ 0.5 % FS", "5 m 处 1× 变焦时表盘成像只有 50 px，指针无法分辨", C.red],
      ["跑得完", "单次巡检 ≤ 30 min", "靠近、放大、多次拍摄都会增加单个航点的停留时间", C.amber],
      ["不误动作", "不能因 AI 误判撞到设备", "模型存在误判，而车辆在运动，停车决策不能由模型直接触发", C.steel],
    ];
    items.forEach(([t, spec, why, col], i) => {
      const x = M + i * 4.13;
      T.card(s, x, y0, 3.87, 2.92);
      s.addShape("ellipse", { x: x + 0.28, y: y0 + 0.28, w: 0.42, h: 0.42,
        fill: { color: col }, line: { color: col, width: 1 } });
      s.addText(String(i + 1), { x: x + 0.28, y: y0 + 0.28, w: 0.42, h: 0.42, align: "center",
        valign: "middle", fontFace: F, fontSize: 12, bold: true, color: C.white, isTextBox: true, margin: 0 });
      s.addText(t, { x: x + 0.84, y: y0 + 0.26, w: 2.8, h: 0.44, fontFace: F, fontSize: 18,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(spec, { x: x + 0.28, y: y0 + 0.86, w: 3.3, h: 0.34, fontFace: F, fontSize: 12,
        bold: true, color: col, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(why, { x: x + 0.28, y: y0 + 1.24, w: 3.32, h: 1.44, fontFace: F, fontSize: 11.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 16 });
    });

    T.card(s, M, y0 + 3.14, W - M * 2, 2.06, C.ink);
    s.addText("三项要求互相冲突，这是本课题的主要难点", {
      x: M + 0.42, y: y0 + 3.40, w: 11.2, h: 0.4, fontFace: F, fontSize: 16, bold: true,
      color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("全程低速高分辨率拍摄能满足精度但超出时间预算；全程高速扫描能满足时间但达不到精度。" +
      "本方案把两者分开：巡航期用轻量检测模型以 30 Hz 扫描，只有检出可疑目标后才停车、对准、" +
      "变焦、重新拍摄，时间开销只发生在需要复核的航点上。是否停车由状态机的抑制规则与安全网关判定，" +
      "模型只提供候选，不直接控制执行器。", {
      x: M + 0.42, y: y0 + 3.90, w: 11.2, h: 1.10, fontFace: F, fontSize: 12.5,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 19 });
    T.foot(s, "指标出处：设计方案书 §2.2 表 2-2 / 测控系统综合实训课题任务书");
    s.addNotes("本页要讲清三项要求之间的冲突。如果评审问为什么不直接换更大的模型，答案是：50 像素的成像里本来就没有足够信息，信息在光学环节已经丢失，只能通过变焦重新采集。");
  }

  // ============================================================ 4 核心立论：像素密度
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "核心立论：像素密度决定读数精度", "同一块压力表在巡航态与复核态下的成像对比");
    s.addImage({ path: IMG + "/zoom_compare.png", x: M, y: y0, w: 12.09, h: 3.92 });

    const st = [["50.0 px", "巡航态 1× 实测框宽", C.red],
                ["150.0 px", "复核态 3× 实测框宽", C.green],
                ["0.4 %", "实测与针孔公式的偏差", C.steel]];
    st.forEach(([v, k, col], i) => T.stat(s, M + i * 3.05, y0 + 4.12, 2.9, v, k, col));

    T.card(s, M + 9.3, y0 + 4.04, 2.79, 1.16, C.amberSoft);
    s.addText("公式算出 49.8 / 149.5 px\n渲染器与光学模型自洽", {
      x: M + 9.55, y: y0 + 4.16, w: 2.4, h: 0.92, fontFace: F, fontSize: 11.5, bold: true,
      color: "8A5A05", isTextBox: true, margin: 0, valign: "middle", lineSpacing: 17 });
    T.foot(s, "复现：python -m patrol.tools.viewer --demo-zoom --out out/   ·   光学公式集中在 patrol/scene/optics.py");
    s.addNotes("左侧巡航态 50 像素，指针无法分辨；右侧 3 倍变焦后 150 像素，刻度可读。要强调的是实测框宽与针孔投影公式的计算值相差 0.4 %，说明虚拟场景在光学上与公式自洽，因此在其上测得的精度指标可以作为依据。");
  }

  // ============================================================ 5 主动式复核流程
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "主动式复核：一次完整的决策闭环", "十状态机。每个状态都定义了超时转移，不存在没有出边的状态");
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
    const y0 = T.head(s, "auto", "系统架构：四进程 + 四接口 + 五份冻结 Schema", "进程边界按安全职责划分：安全网关必须在感知与任务进程异常退出后继续工作");
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

  // ============================================================ 7 无硬件并行开发
  {
    const s = T.slide(pres);
    const y0 = T.head(s, "auto", "硬件没到，怎么把「控」这一半做出来", "虚拟配电室按针孔投影渲染，因此精度与控制指标是实测值而非推导值");
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

};
