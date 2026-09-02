// 第 1–9 页：封面、目录、课题、核心立论、复核流程、架构、无硬件方案、工作量、质量
const T = require("./theme");
const { C, F, W, H, M } = T;

module.exports = function (pres, IMG) {
  // ============================================================ 1 封面
  {
    const s = pres.addSlide();
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
    s.addNotes("开场：这个项目的一句话是——巡航时低成本扫一遍，发现可疑目标就停车、对准、变焦、再看一眼。这一步把表盘从 50 像素放大到 150 像素，读数才够得着 0.5 % FS 的精度要求。这是我们和「拍一路视频回去慢慢看」的根本区别。硬件还没到，但整套系统现在就能在笔记本上跑起来，所有指标都是实测的。");
  }

  // ============================================================ 2 目录
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "目", "汇报提纲", "四个部分，重点在第二、三部分的实测数据");
    const secs = [
      ["01", "课题与技术路线", "为什么必须「停车变焦再看一眼」；像素密度这条立论怎么来的", "3 – 7"],
      ["02", "工作量与质量保障", "代码规模、接口冻结、501 项测试如何守住这套系统", "8 – 11"],
      ["03", "三条识别路线的实测成果", "L1 检测 / L2 分割 / L3 异常，各自的比选结论与数据", "12 – 20"],
      ["04", "系统能力与收口", "安全边界、云台控制、端到端闭环、风险与下一步", "21 – 27"],
    ];
    secs.forEach(([n, t, d, p], i) => {
      const y = y0 + 0.06 + i * 1.31;
      T.card(s, M, y, W - M * 2, 1.14);
      s.addText(n, { x: M + 0.3, y, w: 0.9, h: 1.14, fontFace: F, fontSize: 30, bold: true,
        color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(t, { x: M + 1.24, y: y + 0.20, w: 8.6, h: 0.38, fontFace: F, fontSize: 17,
        bold: true, color: C.text, isTextBox: true, margin: 0, valign: "middle" });
      s.addText(d, { x: M + 1.24, y: y + 0.60, w: 8.9, h: 0.34, fontFace: F, fontSize: 11.5,
        color: C.muted, isTextBox: true, margin: 0, valign: "middle" });
      s.addText("P " + p, { x: W - M - 1.5, y, w: 1.2, h: 1.14, align: "right",
        fontFace: F, fontSize: 12, color: C.steel, isTextBox: true, margin: 0, valign: "middle" });
    });
    T.foot(s, null, 2);
    s.addNotes("提纲四部分。时间紧的话，重点讲 01 的像素密度立论和 03 的三条实测结论。");
  }

  // ============================================================ 3 课题与难点
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "01", "课题：配电室要什么样的巡检", "任务书三项要求，每一项都指向同一个矛盾");
    const items = [
      ["看得清", "表计读数误差 ≤ 0.5 % FS", "5 m 处 1× 变焦，表盘只有 50 px——指针根本读不出来", C.red],
      ["跑得完", "单次巡检 ≤ 30 min", "要读得清就得靠近、放大、多拍，每一样都在花时间", C.amber],
      ["不添乱", "不能因 AI 误判撞到设备", "模型会错，而车是会动的——决策权不能交给模型", C.steel],
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
    s.addText("三项要求互相打架，这就是课题的真正难点", {
      x: M + 0.42, y: y0 + 3.40, w: 11.2, h: 0.4, fontFace: F, fontSize: 16, bold: true,
      color: C.amber, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("「全程慢慢拍」看得清但跑不完；「全程快速扫」跑得完但看不清。我们的解法是把两者分开——" +
      "巡航期用小模型 30 Hz 低成本扫，只在发现可疑目标时才停车、对准、变焦、重拍。" +
      "代价只花在真正需要的地方，而「要不要停车」这个决策由规则和安全网关把关，不交给模型。", {
      x: M + 0.42, y: y0 + 3.90, w: 11.2, h: 1.10, fontFace: F, fontSize: 12.5,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 19 });
    T.foot(s, "指标出处：设计方案书 §2.2 表 2-2 / 测控系统综合实训课题任务书", 3);
    s.addNotes("三项要求互相打架是这一页的核心。评审如果问「为什么不直接用更大的模型」——答案是模型再大也解决不了 50 像素上没有信息这件事。信息在光学环节就丢了，只能靠变焦补回来。");
  }

  // ============================================================ 4 核心立论：像素密度
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "02", "核心立论：像素密度决定读数精度", "全项目最直观的一幕——同一块压力表，变焦前后");
    s.addImage({ path: IMG + "/zoom_compare.png", x: M, y: y0, w: 12.09, h: 3.92 });

    const st = [["50.0 px", "巡航态 1× 实测框宽", C.red],
                ["150.0 px", "复核态 3× 实测框宽", C.green],
                ["0.4 %", "实测与针孔公式的偏差", C.steel]];
    st.forEach(([v, k, col], i) => T.stat(s, M + i * 3.05, y0 + 4.12, 2.9, v, k, col));

    T.card(s, M + 9.3, y0 + 4.04, 2.79, 1.16, C.amberSoft);
    s.addText("公式算出 49.8 / 149.5 px\n渲染器与光学模型自洽", {
      x: M + 9.55, y: y0 + 4.16, w: 2.4, h: 0.92, fontFace: F, fontSize: 11.5, bold: true,
      color: "8A5A05", isTextBox: true, margin: 0, valign: "middle", lineSpacing: 17 });
    T.foot(s, "复现：python -m patrol.tools.viewer --demo-zoom --out out/   ·   光学公式集中在 patrol/scene/optics.py", 4);
    s.addNotes("这张图是整个项目最该记住的一幕。左边巡航态 50 像素，指针根本读不出来；右边 3 倍变焦后 150 像素，刻度清清楚楚。关键是：实测框宽和针孔投影公式算出来的值吻合到 0.4 %，说明我们的虚拟场景不是画着好看，它在光学上是自洽的——所以在它上面测出来的精度指标是有意义的。");
  }

  // ============================================================ 5 主动式复核流程
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "03", "主动式复核：一次完整的决策闭环", "十状态机，每个状态都有超时去处——没有一条出边是空的");
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
    T.foot(s, "实现：patrol/mission/fsm.py（598 行，十状态）· suppress.py（三条抑制）· budget.py（预算与顺延队列）", 5);
    s.addNotes("十个状态里这里画了八个主线状态，另外两个是 ABORT 和 ERROR。设计上有一条硬纪律：没有一个状态的出边是空的，每个状态都有超时去处，所以状态机不会卡死。复核预算那个公式很重要——它保证「主动式」不会把巡检时间拖爆。");
  }

  // ============================================================ 6 架构
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "04", "系统架构：四进程 + 四接口 + 五份冻结 Schema", "拆进程不是为了好看，是为了安全网关能在 AI 崩掉之后继续工作");
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
    s.addText("安全网关必须是唯一能碰执行器的地方，而且必须能在 AI 进程崩掉之后继续工作。" +
      "同一个进程里做不到——一个段错误会把两边一起带走。\n\n" +
      "「杀掉 AI 进程后车自己走完路线」这条演示，验的就是这条边界。", {
      x: M + 8.10, y: y0 + 3.10, w: 3.78, h: 1.86, fontFace: F, fontSize: 11,
      color: C.mutedOnInk, isTextBox: true, margin: 0, valign: "top", lineSpacing: 16 });
    T.foot(s, "五份 JSON Schema 全部 additionalProperties: false；改接口要走 validate.py 的 ALLOWED_DRIFT 白名单流程", 6);
    s.addNotes("这一页回答「为什么这么复杂」。四个进程的边界是按安全职责切的，不是按代码量切的。五份 Schema 是冻结的契约，改一个字段都要走白名单流程，这样四个人可以并行改而不互相踩。");
  }

  // ============================================================ 7 无硬件并行开发
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "05", "硬件没到，怎么把「控」这一半做出来", "虚拟配电室按针孔投影渲染，所以测出来的是真数，不是推导值");
    s.addImage({ path: IMG + "/thirdperson_compare.png", x: M, y: y0, w: 7.55, h: 2.48 });
    s.addText("第三人称机位：车身 + 云台朝向 + 当前视锥。变焦 1×→3× 时视场角从 60.0° 收紧到 21.8°，" +
      "并正好罩住被复核的表盘——「指令下去 → 云台转了 → 视场扫过目标」这条因果链第一次是可见的。", {
      x: M, y: y0 + 2.58, w: 7.55, h: 0.78, fontFace: F, fontSize: 11,
      color: C.muted, isTextBox: true, margin: 0, valign: "top", lineSpacing: 16 });

    const pts = [
      ["桩不是占位符", "主动注入真机上会出现的麻烦：ACK 丢包 2 %、对焦失败 5 %、真实角速度约束、安全事件 0.05 次/分。赋值即到位的假驱动会让任何判据都显得正确。"],
      ["真值与先验严格分开", "world.py 里 truth 只给渲染与打分，感知侧永远读不到——否则精度指标全是自己糊弄自己。这条是整个评测可信度的地基。"],
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
    s.addText("fakecar 起独立进程，字节真的过内核（POSIX 走 PTY，Windows 走 TCP 环回）。" +
      "分帧、CRC、超时、重传、2 % ACK 丢包注入全部照原样发生——证明协议栈与时序逻辑正确。", {
      x: M + 0.28, y: y0 + 4.02, w: 7.0, h: 0.72, fontFace: F, fontSize: 10.5,
      color: "1C6B47", isTextBox: true, margin: 0, valign: "top", lineSpacing: 14 });
    T.foot(s, "虚拟配电室 1 131 行 · 驱动抽象层 2 277 行（四个 ABC + 桩 + 真机串口/V4L2）", 7);
    s.addNotes("这一页是回答「你们没硬件怎么做」的关键。要强调两点：一是桩会主动注入真机上的麻烦，所以在桩上跑通是有意义的验收；二是真值和先验严格分开，感知读不到真值，所以精度数字不是自己骗自己。假小车那条能证明我们的真机代码路径不是死代码。");
  }

  // ============================================================ 8 工作量总览
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "06", "工作量总览", "从零起步，四周内建成一套可运行、可测量、可演示的完整系统");
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
    T.foot(s, "统计口径：git 仓库实测，不含空行与第三方依赖", 8);
    s.addNotes("这一页专门讲工作量。要强调的不是行数本身，而是结构——测试 5900 行几乎和核心业务代码一样多，说明质量不是最后补的。文档 4700 行意味着这套系统别人接得住。");
  }

  // ============================================================ 9 质量保障
  {
    const s = pres.addSlide();
    const y0 = T.head(s, "07", "质量保障：怎么保证这套系统不是「看起来能跑」", "三道闸门，每次提交都要全过");
    const gates = [
      ["接口一致性校验", "51 项", "Schema 与代码、网关硬编码常量与 Schema 范围逐条交叉比对。" +
        "含 9 条反例——故意构造越界报文，必须全部被拦下。", C.amber, "python -m patrol.tools.validate"],
      ["自动化测试", "501 项", "覆盖率 75 %。含端到端 300 s 全链路、串口协议往返、状态机超时、" +
        "网关五项校验、云端台账、证据保留策略。", C.green, "python -m pytest -q"],
      ["端到端实跑", "300 s", "起全系统跑一轮真实巡检，产出证据包并统计复核增益。" +
        "跑不出数就是没跑通——不接受「理论上可以」。", C.steel, "python -m patrol.tools.run_all --seconds 300"],
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
    s.addText("一条贯穿全项目的纪律：测不到的写「未测」，测出来难看的如实写", {
      x: M + 0.34, y: y0 + 3.70, w: 11.4, h: 0.36, fontFace: F, fontSize: 14.5, bold: true,
      color: "8A5A05", isTextBox: true, margin: 0, valign: "middle" });
    s.addText("举例：重复性指标 0.321 % FS 压线超差（限值 0.3），我们把它挂在 README 首页而不是调参数凑过去——" +
      "那等于把误差藏起来。评审最容易问倒人的不是「你这个指标为什么低」，而是「你这个数是怎么来的」。", {
      x: M + 0.34, y: y0 + 4.10, w: 11.4, h: 0.82, fontFace: F, fontSize: 11.5,
      color: "8A5A05", isTextBox: true, margin: 0, valign: "top", lineSpacing: 16 });
    T.foot(s, "三道闸门在每次提交前全跑；validate 不过就不提交", 9);
    s.addNotes("这一页讲我们怎么守质量。三道闸门都是命令行一条就能跑的，可以当场演示。最后那句纪律很重要——重复性超差我们主动写在 README 首页，这个态度本身就是答辩的加分项。");
  }
};
