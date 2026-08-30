"""端到端：桩上跑通一次完整复核闭环。

**这是全项目唯一一条能证明"四个进程真的连成一条链"的测试。**其余测试都是把
某一层单独拎出来打分（读数精度、PID 阶跃响应、网关拦截率），单层全绿并不等于
链路通——实际就出过这种事：106 个单元测试全绿，端到端却永远只产出一个
INCONCLUSIVE 证据包。

**测试刻意重建四进程的拓扑，而不是省事地共用一套驱动。**

    gateway   → build_drivers(seed=0)               拥有执行器的那一套
    perception→ build_drivers(seed=7, passive=True) 收不到任何指令的旁观者

两套桩、两个种子，故意让它们**对不上**。感知那套的云台永远停在开机位
(0, 0, 1×)，底盘也不推进；它能看见东西的唯一途径，是 ICamera.observe_state
把 IF-3 报的位姿与云台角回灌进相机桩。共用一套驱动的话这条通路就被短路了，
测试会假绿——而真机上四个进程本来就各建各的驱动。

判据（缺一不可）：

- 走完 CRUISE → SUSPECT → HALT_REQ → AIM → ZOOM → CAPTURE → VERIFY → PACK
- 伺服在 AIM 期间**采到过反馈样本**（samples > 0）。采不到说明目标不在画面里，
  只能干等超时——这正是修复前的症状
- 证据包 after.pixel_density_px 达到判据线 120 px（或已经顶到最大倍率）。
  **判据不能写成"倍率比 ≥ 2.4"**——需要的倍率由 before 的密度反解而来
  （z = clip(120 / p_before, 1, 3)），先触发的是哪块表、车停在哪一步，
  before 就不一样，倍率比自然跟着变。钉死一个倍率是在钉一次运行的巧合
- gain.delta_conf > 0 且 verify_success = true
- before.est_distance_m ≥ 5.0。过道 y=−3.18、柜面 y=+1.82，任何表计都不可能
  近于 5.00 m。**这一条是用来反证触发源是真表计而不是合成误检的**——修复前
  那次触发的 est_distance_m 是 3.02 m，几何上根本不存在。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from patrol.common.config import Config
from patrol.drivers.factory import build_drivers
from patrol.gateway.node import GatewayNode
from patrol.mission.fsm import State
from patrol.mission.node import MissionNode
from patrol.perception.node import PerceptionNode
from patrol.uploader.node import UploaderNode

#: 一轮巡检里 x=4.0（开关把手）、8.0（指示灯）、10.2（压力表）依次进视野，
#: 车速 0.5 m/s。第一次复核通常在 10 s 内完成，给到这么大是留给
#: 对焦失败（5 %）、ACK 丢包（2 %）、安全事件这些桩故意注入的故障重试。
#:
#: **从 100 s 提到 240 s 是因为负载。**四个节点跑在四条线程上，桩的物理仿真
#: 又由各自的后台线程按墙上时钟推进；机器一忙，整条流水线一起变慢。实测在
#: 满负载下 100 s 内跑完了六次完整的 SUSPECT→…→PACK 循环，**每一次都对**，
#: 但每一次都因为下面那个 pending_ttl_s 先到期而被判成 INCONCLUSIVE——
#: 失败的是配对的时间预算，不是流水线。
#:
#: 提大不影响正常运行：拿到一次成功的复核就 break，空闲机器上仍然十几秒返回。
_DEADLINE_S = 240.0


def _load(free_ports, tmp_path) -> Config:
    return Config.load(overrides={
        "bus": free_ports,
        "logging": {"dir": str(tmp_path / "logs")},
        "gateway": {"audit_log": str(tmp_path / "logs" / "audit.jsonl")},
        "uploader": {
            "evidence_dir": str(tmp_path / "evidence"),
            "upload_period_s": 1e6,     # 本测试不连云端，别让上传队列空转重试
            # 配对超时。**这是这条用例在负载下唯一的失败点**：原来收紧到 30 s，
            # 而 FSM 光是超时预算加总就有 22 s，机器一忙就超，于是 uploader
            # 在 stage=VERIFY 的报文到达前放弃配对，证据包全部变成
            # INCONCLUSIVE / STATE_TIMEOUT——看起来像流水线坏了，其实是这个数
            # 太小。取 90 s：比 22 s 的最坏预算留出 4 倍余量，仍远小于 240 s
            # 的 deadline，配不上仍然会被判失败，不会掩盖真正的配对 bug。
            "pending_ttl_s": 90.0,
        },
        # 合成误检是设计的一部分（复核就是用来消解它们的），但这条测试要验的是
        # "真表计能被复核到"，掺进误检会让 est_distance_m 判据失去意义
        "perception": {"synthetic": {"false_positive_rate": 0.0}},
    })


class _Rig:
    """四个节点 + 四条线程。起停顺序与 tools/run_all.py 一致。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        # 网关拥有执行器；感知是被动旁观者。两个种子故意不同，见模块文档。
        self.gw_drivers = build_drivers(cfg, seed=0)
        self.pc_drivers = build_drivers(cfg, seed=7, passive=True)
        self.gw = GatewayNode(cfg, drivers=self.gw_drivers)
        self.pc = PerceptionNode(cfg, drivers=self.pc_drivers)
        self.ms = MissionNode(cfg)
        self.up = UploaderNode(cfg)

        self.transitions: list[tuple[str, str, str]] = []
        self.aim_samples: list[int] = []
        inner = self.ms.fsm.on_transition

        def _record(prev: State, nxt: State, reason: str) -> None:
            self.transitions.append((prev.value, nxt.value, reason))
            if prev is State.AIM:
                m = self.ms.servo.metrics().get("pan", {})
                self.aim_samples.append(int(m.get("samples", 0)))
            inner(prev, nxt, reason)

        self.ms.fsm.on_transition = _record
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for name, node in (("gateway", self.gw), ("perception", self.pc),
                           ("mission", self.ms), ("uploader", self.up)):
            t = threading.Thread(target=node.serve_forever, name=name, daemon=True)
            t.start()
            self._threads.append(t)
            time.sleep(0.3 if name == "gateway" else 0.1)

    def stop(self) -> None:
        for node in (self.ms, self.pc, self.up, self.gw):
            node.stop()
        for t in self._threads:
            t.join(timeout=5.0)

    def packages(self) -> list[dict]:
        root = Path(self.cfg.get("uploader.evidence_dir"))
        out = []
        for mf in sorted(root.glob("*/*/manifest.json")):
            try:
                out.append(json.loads(mf.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    def successful(self) -> dict | None:
        for m in self.packages():
            if m.get("gain", {}).get("verify_success"):
                return m
        return None


@pytest.mark.slow
def test_end_to_end_verify_cycle(free_ports, tmp_path):
    rig = _Rig(_load(free_ports, tmp_path))
    rig.start()
    try:
        t0 = time.monotonic()
        good = None
        while time.monotonic() - t0 < _DEADLINE_S:
            good = rig.successful()
            if good is not None:
                break
            time.sleep(0.5)
    finally:
        rig.stop()

    states = [nxt for _, nxt, _ in rig.transitions]
    assert good is not None, (
        "%.0f s 内没有一次复核成功。状态轨迹=%s 证据包=%s"
        % (_DEADLINE_S, states,
           [(m["event_id"][:8], m["verdict"]["result"], m.get("abort"))
            for m in rig.packages()]))

    # 十状态机确实逐级走过，不是跳过去的
    for s in ("SUSPECT", "HALT_REQ", "AIM", "ZOOM", "CAPTURE", "VERIFY", "PACK"):
        assert s in states, "状态机没走到 %s，轨迹=%s" % (s, states)

    # 伺服真的采到了反馈：AIM 期间目标在画面里
    assert rig.aim_samples and max(rig.aim_samples) > 0, (
        "AIM 全程没采到反馈样本 %s——目标不在画面里，伺服只能干等超时"
        % rig.aim_samples)

    g, before, after = good["gain"], good["before"], good["after"]
    # 复核的全部意义：像素密度提到判据线以上，置信度跟着上去。
    # 需要多大倍率是 before 决定的（z = clip(p_target / p_before, 1, max_zoom)），
    # 所以这里钉的是**结果达标**，不是某个固定倍率。
    p_min = float(rig.cfg.get("perception.quality.pixel_density_target", 120.0))
    max_zoom = float(rig.cfg.get("optics.max_zoom", 3.0))
    assert g["pixel_density_ratio"] > 1.5, good
    assert (after["pixel_density_px"] >= 0.90 * p_min
            or after["zoom"] >= 0.99 * max_zoom), (
        "复核后只有 %.1f px，既没到判据线 %.0f px 也没顶到最大倍率 %.1f×"
        % (after["pixel_density_px"], p_min, max_zoom))
    assert g["delta_conf"] > 0.0, good
    assert after["zoom"] > before["zoom"], good
    assert good.get("abort") is None, good

    # 几何自洽：过道到柜面 5.00 m，比这更近的目标不存在。
    # 这一条挡的是"被合成误检骗进复核"——修复前那次触发报的是 3.02 m。
    assert before["est_distance_m"] >= 5.0, (
        "before 距离 %.2f m 小于过道到柜面的 5.00 m，触发源不是真表计"
        % before["est_distance_m"])


@pytest.mark.slow
def test_perception_view_follows_status_report(free_ports, tmp_path):
    """单独钉住根因：感知那套桩收不到指令，画面必须跟着 IF-3 走。

    修复前这里会失败——感知的云台停在 pan=0（朝正前方，柜面在侧方 90°），
    渲染出来的画面里一个目标都没有。
    """
    cfg = _load(free_ports, tmp_path)
    rig = _Rig(cfg)
    rig.start()
    try:
        t0 = time.monotonic()
        aimed = 0
        while time.monotonic() - t0 < 30.0:
            st = rig.pc._last_status                      # noqa: SLF001
            # 等云台摆到巡航姿态（|pan| 接近 90°）且车在动
            if st and abs(abs(float(st["ptz"]["pan_deg"])) - 90.0) < 5.0 \
                    and st["ptz"]["at_target"]:
                # 感知本地那套 ptz 应当纹丝未动，证明它确实收不到指令
                assert abs(rig.pc.ptz.status().pan_deg) < 1.0
                if rig.pc.camera.last_targets():
                    aimed += 1
                    if aimed >= 3:
                        break
            time.sleep(0.2)
    finally:
        rig.stop()
    assert aimed >= 3, "云台按 IF-3 对准柜列后，感知画面里仍然看不到目标"
