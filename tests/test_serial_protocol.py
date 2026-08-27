"""底盘串口：编解码 + 假小车往返。docs/底盘串口协议.md v0.1。

硬件没到，但协议是我们定的，所以这条链路现在就能验：`ChassisSerial` 与
`FakeCar` 通过内存回环对接，跑完整的 PAUSE → 停稳 → RESUME，并验证
越界 CRP 被底盘固件**独立**拒掉、安全事件走独立通道、校验失败的帧被丢弃。

**两侧共用同一份编解码**（`serial_protocol.py`），所以这些用例同时也是
协议文档的可执行版本：改了字段两边一起挂，不会出现"文档一套、实现两套"。
"""
from __future__ import annotations

import threading
import time

import pytest

from patrol.common.config import Config
from patrol.drivers.base import ChassisState, ExecProgress, ParamOutOfRange
from patrol.drivers.real import serial_protocol as P
from patrol.drivers.real.chassis_serial import ChassisSerial
from patrol.drivers.real.serial_link import LoopbackLink
from patrol.tools.fakecar import FakeCar


# ---------------------------------------------------------------- 编解码
def test_roundtrip_preserves_fields():
    raw = P.encode(P.CMD_GOTO, 42, "WP-07", 300)
    f = P.decode(raw)
    assert (f.type, f.seq, f.fields) == ("GTO", 42, ("WP-07", "300"))


def test_crc_rejects_single_bit_flip():
    raw = bytearray(P.encode(P.CMD_PAUSE, 7, "VERIFY_REQUEST"))
    raw[6] ^= 0x01                       # 翻 payload 里的一个比特
    with pytest.raises(P.ProtocolError):
        P.decode(bytes(raw))


def test_seq_wraps_at_16_bits():
    assert P.decode(P.encode(P.CMD_PING, 65536)).seq == 0
    assert P.decode(P.encode(P.CMD_PING, 65535)).seq == 65535


def test_status_frame_is_all_integers_on_the_wire():
    """协议里全部走毫米/千分比，避免两端的浮点格式对不上。"""
    raw = P.encode_status(1, state="STOPPING", speed_mps=0.237,
                          path_progress=0.4567, distance_to_goal_m=1.2345,
                          waypoint_id="WP-04", battery_pct=81.3,
                          safety_active=True)
    assert b"." not in raw.split(b"*")[0]
    st = P.parse_status(P.decode(raw))
    assert st["state"] == "STOPPING"
    assert st["speed_mps"] == pytest.approx(0.237, abs=1e-3)
    assert st["distance_to_goal_m"] == pytest.approx(1.2345, abs=1e-3)
    assert st["current_waypoint_id"] == "WP-04"
    assert st["safety_layer_active"] is True


def test_no_goal_is_minus_one_not_zero():
    """0 mm 是"就在目标上"，−1 才是"没有目标"。混用会让状态机以为到位了。"""
    raw = P.encode_status(1, state="MOVING", speed_mps=0.5, path_progress=0.1,
                          distance_to_goal_m=None, waypoint_id=None,
                          battery_pct=90.0, safety_active=False)
    st = P.parse_status(P.decode(raw))
    assert st["distance_to_goal_m"] is None
    assert st["current_waypoint_id"] is None


def test_line_reader_resyncs_after_garbage():
    r = P.LineReader()
    good = P.encode(P.CMD_RESUME, 3)
    lines = list(r.feed(b"\x00\xffnoise" + b"\n" + good))
    assert len(lines) == 2                       # 噪声行 + 好行
    with pytest.raises(P.ProtocolError):
        P.decode(lines[0])
    assert P.decode(lines[1]).type == "RES"


def test_line_reader_handles_split_frames():
    """串口读回来的是任意长度的片段，不是行。"""
    r = P.LineReader()
    raw = P.encode(P.CMD_PAUSE, 9, "X")
    assert list(r.feed(raw[:4])) == []
    out = list(r.feed(raw[4:]))
    assert len(out) == 1 and P.decode(out[0]).seq == 9


def test_safety_frame_keeps_over_limit_brake_latency():
    """brake_ms 超过 100 ms 也照样上报——那正是最该留证的数（协议 §3.4）。"""
    raw = P.encode_safety(5, event="BUMPER_HIT", severity="CRITICAL",
                          brake_ms=180, detail="实测超限")
    ev = P.parse_safety(P.decode(raw))
    assert ev["brake_latency_ms"] == 180
    assert ev["event_type"] == "BUMPER_HIT"


def test_protocol_has_no_motion_primitives():
    """**协议里不存在转向角/轮速/扭矩/制动力。**

    "AI 侧无法直接控制车辆运动"这条由协议本身保证，不靠代码评审保证
    （ICD §4.1）。这条用例把它钉住：上位机能发的指令码就这六个。
    """
    uplink = {P.CMD_PAUSE, P.CMD_RESUME, P.CMD_CREEP, P.CMD_GOTO,
              P.CMD_QUERY, P.CMD_PING}
    assert len(uplink) == 6
    src = open("patrol/drivers/real/serial_protocol.py", encoding="utf-8").read()
    for banned in ("steer", "wheel_speed", "torque", "brake_force", "target_speed"):
        assert banned not in src.lower().replace("brake_latency", "")


# ---------------------------------------------------------------- 往返
class _Rig:
    def __init__(self, tmp_path, seed=0):
        self.cfg = Config.load(overrides={
            "logging": {"dir": str(tmp_path / "logs")},
            "real": {"serial": {"chassis": {"ping_period_s": 0.05,
                                            "status_timeout_s": 1.0}}},
        })
        self.loop = LoopbackLink()
        self.car = FakeCar(self.cfg, self.loop.side_b(), seed=seed)
        self.chassis = ChassisSerial(self.cfg, link=self.loop.side_a())
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._pump, daemon=True)
        self._t.start()

    def _pump(self):
        while not self._stop.wait(0.005):
            self.car.step()

    def wait(self, pred, timeout_s=6.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            try:
                if pred():
                    return True
            except Exception:            # noqa: BLE001 - 状态帧还没到
                pass
            time.sleep(0.02)
        return False

    def pause_pump(self):
        """让底盘停止发状态帧，用于验证"状态过期"的处理。"""
        self._stop.set()
        self._t.join(timeout=2)

    def close(self):
        self._stop.set()
        self._t.join(timeout=2)
        self.chassis.close()
        self.car.close()


class _FirmwareRig:
    """只起假小车，测试自己扮演上位机，看得到链路上的每一帧原始字节。

    验"底盘固件自己那道限幅"必须这样测：走 ChassisSerial 的话越界指令在
    上位机就被挡下了，根本到不了底盘，第二道防线等于没被测过。
    """

    def __init__(self, tmp_path, seed=0):
        self.cfg = Config.load(overrides={"logging": {"dir": str(tmp_path / "logs")}})
        self.loop = LoopbackLink()
        self.me = self.loop.side_a()
        self.car = FakeCar(self.cfg, self.loop.side_b(), seed=seed)
        self.reader = P.LineReader()

    def pump(self, seconds=1.0):
        """跑一段时间，把链路上收到的帧解出来。"""
        out = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            self.car.step()
            for line in self.reader.feed(self.me.read()):
                try:
                    out.append(P.decode(line))
                except P.ProtocolError:
                    pass
            time.sleep(0.005)
        return out

    def close(self):
        self.car.close()


@pytest.fixture()
def rig(tmp_path):
    r = _Rig(tmp_path)
    try:
        yield r
    finally:
        r.close()


def test_status_frames_arrive_and_decode(rig):
    assert rig.wait(lambda: rig.chassis.status().state is ChassisState.MOVING)
    st = rig.chassis.status()
    assert 0.0 <= st.battery_pct <= 100.0
    assert st.current_waypoint_id


def test_pause_then_resume_round_trip(rig):
    assert rig.wait(lambda: rig.chassis.status().state is ChassisState.MOVING)
    h = rig.chassis.pause("VERIFY_REQUEST")
    # 停车延迟 1.5–2.5 s 是桩故意注入的，超出 2.0 s 预算但在 4.0 s 超时内
    assert rig.wait(lambda: rig.chassis.status().state is ChassisState.STOPPED,
                    timeout_s=8.0), "底盘没有在 8 s 内停稳"
    assert rig.wait(lambda: rig.chassis.poll(h).progress is ExecProgress.DONE,
                    timeout_s=2.0), "PAUSE 的句柄没有结算成 DONE"

    rig.chassis.resume()
    assert rig.wait(lambda: rig.chassis.status().state is ChassisState.MOVING,
                    timeout_s=4.0)


def test_upper_side_rejects_over_limit_creep(rig):
    """第一道：驱动层按硬件能力校验，越界抛异常**不截断**（ICD §8.1 第二条）。"""
    assert rig.wait(lambda: rig.chassis.status().state is ChassisState.MOVING)
    with pytest.raises(ParamOutOfRange):
        rig.chassis.creep_forward(0.9)


def test_chassis_firmware_rejects_over_limit_creep_independently(tmp_path):
    """第二道：绕过上位机直接往链路上灌越界的 CRP，底盘必须自己拒掉。

    两道限幅独立生效，是纵深防御不是冗余——上位机被改坏了，车也不会窜出去。
    """
    rig = _FirmwareRig(tmp_path)
    try:
        rig.me.write(P.encode(P.CMD_CREEP, 60000, 900))       # 900 mm > 500 mm
        rig.me.write(P.encode(P.CMD_CREEP, 60001, 200))       # 合法值做对照
        acks = {f.int_field(0): f.field(1)
                for f in rig.pump(1.0) if f.type == P.RSP_ACK}
        assert acks.get(60000) == P.ACK_REJECT, "底盘固件没有拒绝越界的 CRP"
        assert acks.get(60001) == P.ACK_OK, "合法的 CRP 不该被拒"
    finally:
        rig.close()


def test_chassis_firmware_rejects_unknown_waypoint(tmp_path):
    rig = _FirmwareRig(tmp_path)
    try:
        rig.me.write(P.encode(P.CMD_GOTO, 40000, "WP-99", 300))
        acks = {f.int_field(0): f.field(1)
                for f in rig.pump(1.0) if f.type == P.RSP_ACK}
        assert acks.get(40000) == P.ACK_REJECT
    finally:
        rig.close()


def test_corrupt_frame_is_dropped_without_ack(tmp_path):
    """校验失败的帧一律丢弃，不回执、不猜——上位机靠超时发现（协议 §2）。"""
    rig = _FirmwareRig(tmp_path)
    try:
        bad = bytearray(P.encode(P.CMD_PAUSE, 40010, "X"))
        bad[-4] ^= 0x01                                # 破坏 CRC
        rig.me.write(bytes(bad))
        acks = [f for f in rig.pump(0.8) if f.type == P.RSP_ACK]
        assert not acks, "坏帧不该产生回执"
    finally:
        rig.close()


def test_keepalive_timeout_stops_the_car(tmp_path):
    """上位机不发 PNG 超过 3 s，底盘自行减速停车。

    这是**底盘侧**的独立保护，与网关看门狗是两回事：网关看门狗管"AI 进程
    死了"，保活管"串口断了或整个 RK3576 死了"——后者网关自己也救不了。
    """
    cfg = Config.load(overrides={"logging": {"dir": str(tmp_path / "logs")},
                                 "real": {"serial": {"chassis": {"keepalive_s": 0.3}}}})
    loop = LoopbackLink()
    me, car = loop.side_a(), FakeCar(cfg, loop.side_b())
    reader = P.LineReader()
    try:
        states = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 4.0:
            car.step()
            for line in reader.feed(me.read()):
                try:
                    f = P.decode(line)
                except P.ProtocolError:
                    continue
                if f.type == P.RSP_STATUS:
                    states.append(P.parse_status(f)["state"])
            if "STOPPED" in states:
                break
            time.sleep(0.005)
        assert "STOPPED" in states, "保活超时后底盘没有停下来，实测状态序列 %s" % (
            sorted(set(states)),)
    finally:
        car.close()


def test_safety_event_reaches_callback(rig):
    seen: list[dict] = []
    rig.chassis.subscribe_safety(seen.append)
    assert rig.wait(lambda: rig.chassis.status() is not None)
    rig.car.chassis.force_safety_event("OBSTACLE_DETECTED")
    assert rig.wait(lambda: bool(seen), timeout_s=2.0), "安全事件没有走到回调"
    assert seen[0]["event_type"] == "OBSTACLE_DETECTED"
    assert seen[0]["source"] == "CHASSIS_SAFETY_LAYER"


def test_stale_status_reports_fault(rig):
    """状态帧过期不能当新鲜的用。

    上位机据此判断"车停稳了"，拿过期数据会在车还在动的时候开始变焦抓拍。
    宁可报 FAULT 让状态机走超时路径。
    """
    assert rig.wait(lambda: rig.chassis.status().state is ChassisState.MOVING)
    rig.pause_pump()                     # 底盘不再发状态帧
    time.sleep(rig.chassis.status_timeout_s + 0.3)
    assert rig.chassis.status().state is ChassisState.FAULT
