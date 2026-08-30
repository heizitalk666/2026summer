"""云台真机驱动：串口往返。

这个模块此前**覆盖率是 0**——写完从没被执行过。上一轮那个俯仰符号 bug 就是
藏在没被测过的地方，所以这里补一个说同一套协议的假云台，把整条链路跑起来。

假云台按真实云台的物理约束动：角速度上限、变焦耗时、到位抖动。这样
`poll()` 的"到位判据"才有意义——对着一个"赋值即到位"的假云台，什么判据
都是对的。
"""
from __future__ import annotations

import threading
import time

import pytest

from patrol.common.config import Config
from patrol.drivers.base import (ExecProgress, FocusState, ParamOutOfRange,
                                 PTZSpeed)
from patrol.common.errors import DriverNotReady
from patrol.drivers.real import ptz_serial as PS
from patrol.drivers.real import serial_protocol as P
from patrol.drivers.real.ptz_serial import PTZSerial
from patrol.drivers.real.serial_link import LoopbackLink


class FakeGimbal:
    """说 PTZ v0.1 协议的仿真云台。20 Hz 发位姿帧。

    **必须有真实的运动约束。**赋值即到位的假云台会让任何到位判据都显得正确，
    包括"一发指令就报完成"这种错的。
    """

    PAN_DPS, TILT_DPS = 60.0, 40.0
    ZOOM_PER_S = 1.5
    TOL = 0.05

    def __init__(self, link, *, focus_ms: float = 0.30):
        self.link = link
        self.pan = self.tilt = 0.0
        self.zoom = 1.0
        self.t_pan = self.t_tilt = 0.0
        self.t_zoom = 1.0
        self.rate = None                      # (pan_dps, tilt_dps, expire_monotonic)
        self.focus = FocusState.LOCKED
        self._focus_until = 0.0
        self.focus_ms = focus_ms
        self.force_focus_fail = False        # 注入 5 % 对焦失败那条路径
        self._seq = 0
        self._reader = P.LineReader()
        self._last_report = 0.0
        self._last_tick = time.monotonic()

    # -- 指令 --------------------------------------------------------
    def _handle(self, f: P.Frame) -> None:
        if f.type == PS.CMD_SET:
            self.t_pan = f.int_field(0) / 1000.0
            self.t_tilt = f.int_field(1) / 1000.0
            z = max(1.0, f.int_field(2, 100) / 100.0)
            if abs(z - self.zoom) > self.TOL and not self.force_focus_fail:
                self.focus = FocusState.FOCUSING
                self._focus_until = time.monotonic() + self.focus_ms
            self.t_zoom = z
            self.rate = None
        elif f.type == PS.CMD_RATE:
            ttl = max(0, f.int_field(2, 300)) / 1000.0
            self.rate = (f.int_field(0) / 1000.0, f.int_field(1) / 1000.0,
                         time.monotonic() + ttl)
        elif f.type == PS.CMD_HOME:
            self.t_pan = self.t_tilt = 0.0
            self.t_zoom = 1.0
            self.rate = None

    # -- 物理 --------------------------------------------------------
    def _advance(self, dt: float) -> None:
        if self.force_focus_fail:
            self.focus = FocusState.FAILED
        if self.rate is not None:
            pdps, tdps, expire = self.rate
            if time.monotonic() >= expire:
                self.rate = None              # ttl 自失效，由云台自己执行
            else:
                self.pan += pdps * dt
                self.tilt += tdps * dt
                return
        for attr, tgt, dps in (("pan", self.t_pan, self.PAN_DPS),
                               ("tilt", self.t_tilt, self.TILT_DPS)):
            cur = getattr(self, attr)
            step = dps * dt
            setattr(self, attr, tgt if abs(tgt - cur) <= step
                    else cur + (step if tgt > cur else -step))
        if abs(self.t_zoom - self.zoom) > 1e-6:
            step = self.ZOOM_PER_S * dt
            self.zoom = (self.t_zoom if abs(self.t_zoom - self.zoom) <= step
                         else self.zoom + (step if self.t_zoom > self.zoom else -step))
        if self.focus is FocusState.FOCUSING and time.monotonic() >= self._focus_until \
                and abs(self.t_zoom - self.zoom) <= 1e-6:
            self.focus = FocusState.LOCKED

    @property
    def at_target(self) -> bool:
        return (self.rate is None
                and abs(self.pan - self.t_pan) <= self.TOL
                and abs(self.tilt - self.t_tilt) <= self.TOL
                and abs(self.zoom - self.t_zoom) <= self.TOL)

    @property
    def moving(self) -> bool:
        return not self.at_target or self.rate is not None

    def step(self) -> None:
        now = time.monotonic()
        dt, self._last_tick = now - self._last_tick, now
        try:
            chunk = self.link.read()
        except OSError:
            chunk = b""
        for line in self._reader.feed(chunk):
            try:
                self._handle(P.decode(line))
            except P.ProtocolError:
                continue
        self._advance(max(0.0, dt))
        if now - self._last_report >= 0.05:               # 20 Hz
            self._last_report = now
            self._seq = (self._seq + 1) & 0xFFFF
            self.link.write(PS.encode_pose_report(
                self._seq, pan_deg=self.pan, tilt_deg=self.tilt, zoom=self.zoom,
                moving=self.moving, focus=self.focus, at_target=self.at_target))


class _Rig:
    def __init__(self, tmp_path, **gimbal_kw):
        self.cfg = Config.load(overrides={"logging": {"dir": str(tmp_path / "logs")}})
        self.loop = LoopbackLink()
        self.gimbal = FakeGimbal(self.loop.side_b(), **gimbal_kw)
        self.ptz = PTZSerial(self.cfg, link=self.loop.side_a())
        self._stop = threading.Event()
        self._t = threading.Thread(
            target=lambda: [self.gimbal.step()
                            for _ in iter(lambda: self._stop.wait(0.005), True)],
            daemon=True)
        self._t.start()

    def wait(self, pred, timeout_s=6.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            try:
                if pred():
                    return True
            except Exception:                              # noqa: BLE001
                pass
            time.sleep(0.02)
        return False

    def close(self):
        self._stop.set()
        self._t.join(timeout=2)
        self.ptz.close()


@pytest.fixture()
def rig(tmp_path):
    r = _Rig(tmp_path)
    try:
        yield r
    finally:
        r.close()


# ---------------------------------------------------------------- 基本
def test_pose_frames_arrive(rig):
    assert rig.wait(lambda: rig.ptz.status() is not None)
    st = rig.ptz.status()
    assert st.zoom == pytest.approx(1.0)
    assert st.hfov_deg == pytest.approx(60.0, abs=0.1)


def test_status_before_any_frame_raises_not_ready(tmp_path):
    """还没收到位姿帧时必须报 DriverNotReady，不能编一个默认位姿出来。"""
    loop = LoopbackLink()
    ptz = PTZSerial(Config.load(overrides={"logging": {"dir": str(tmp_path)}}),
                    link=loop.side_a())
    try:
        with pytest.raises(DriverNotReady):
            ptz.status()
    finally:
        ptz.close()


def test_set_pose_actually_moves_the_gimbal(rig):
    """**等的条件必须就是断言的条件，而且"到位"要连目标一起看。**

    这条用例踩了两次，两次都是等待条件写得比断言松：

    1. 先等 |pan − 90| < 0.5、再断言 at_target。0.5° 这个窗口比云台自己的
       到位判据更松，实测 pan = 89.52 时等待就通过了，而云台还在 moving。
    2. 改成只等 at_target。可刚发出 set_pose 的那一瞬间云台还没开始动，
       位姿帧报的是**上一个目标**的"已到位"——于是在 pan = 0 就通过了。
       这正是 test_poll_does_not_trust_a_stale_at_target 记录的同一个陷阱，
       驱动侧靠 _pose_matches() 防它，用例这边也得防。

    所以等的就是"到位**且**落在本条指令的目标上"，和驱动的判据一致。
    """
    assert rig.wait(lambda: rig.ptz.status() is not None)
    rig.ptz.set_pose(90.0, 2.0, 1.0, PTZSpeed.NORMAL)
    arrived = lambda: (rig.ptz.status().at_target                    # noqa: E731
                       and abs(rig.ptz.status().pan_deg - 90.0) <= 0.5)
    assert rig.wait(arrived, 4.0), \
        "4 s 内没有转到位，当前 pan=%.2f at_target=%s" % (
            rig.ptz.status().pan_deg, rig.ptz.status().at_target)


def test_hfov_narrows_with_zoom(rig):
    """hfov(z) = 2·arctan(tan(θ₀/2)/z)。3× 时约 21.79°，**不是** 60/3 = 20°。

    差的这 1.79° 不是舍入：视场角与倍率不成反比，成反正切关系。按 20° 算
    像素密度会系统性偏大 9 %，正好把"够不够 120 px"的判据推到错误的一侧。
    """
    assert rig.wait(lambda: rig.ptz.status() is not None)
    rig.ptz.set_pose(0.0, 0.0, 3.0, PTZSpeed.NORMAL)
    assert rig.wait(lambda: rig.ptz.status().zoom > 2.95, 4.0)
    import math

    # 标称值本身要钉住：3× 时是 21.79°，不是 60/3 = 20°
    expect_3x = math.degrees(2 * math.atan(math.tan(math.radians(30.0)) / 3.0))
    assert expect_3x == pytest.approx(21.79, abs=0.01)

    # 但下面这条断言要对着**实测到的那个 zoom** 算，不是对着标称的 3.0。
    #
    # 原来是拿 ±0.15° 的容差去比标称值，而放行门槛只要求 zoom > 2.95：
    # ±0.15° 实际要求 zoom ≥ 2.979，于是 (2.95, 2.979) 这一段是"门槛放行、
    # 断言必挂"的窗口。伺服还没完全收敛时就会落进去，表现为随机失败——
    # 而 hfov 与 zoom 成反正切、不成线性，所以这个窗口不是靠调容差能消掉的。
    #
    # 按实测 zoom 反算，这条关系在**任何** zoom 上都必须成立，race 就没有了，
    # 容差反而能收紧到 0.02°。
    st = rig.ptz.status()          # 只取一次：zoom 与 hfov 必须来自同一个采样，
                                   # 分两次读的话中间伺服又动了，race 会原样回来
    expect = math.degrees(2 * math.atan(math.tan(math.radians(30.0)) / st.zoom))
    assert st.hfov_deg == pytest.approx(expect, abs=0.02)


# ---------------------------------------------------------------- poll
def test_poll_is_not_done_the_instant_the_command_is_sent(rig):
    """**这条钉的是一个真出现过的 bug。**

    `poll()` 原来只看位姿帧里的 at_target。可刚发出 set_pose 的那一瞬间，
    云台还没开始动，位姿帧报的仍是上一个目标的"已到位"——于是这条指令一
    发出去就被判成 DONE，exec 进度全是假的。判据必须是"报回来的位姿确实
    落在本条指令的目标上"。
    """
    assert rig.wait(lambda: rig.ptz.status() is not None)
    assert rig.wait(lambda: rig.ptz.status().at_target, 3.0)   # 先在原位到位

    h = rig.ptz.set_pose(120.0, 20.0, 1.0, PTZSpeed.NORMAL)    # 120° 要转 2 s
    assert rig.ptz.poll(h).progress is ExecProgress.IN_PROGRESS, \
        "指令刚发出就报完成——poll 又只看 at_target 了"
    time.sleep(0.15)
    assert rig.ptz.poll(h).progress is ExecProgress.IN_PROGRESS

    assert rig.wait(lambda: rig.ptz.poll(h).progress is ExecProgress.DONE, 6.0)
    st = rig.ptz.status()
    assert abs(st.pan_deg - 120.0) < 0.5 and abs(st.tilt_deg - 20.0) < 0.5


def test_poll_waits_for_focus_lock_after_zoom(rig):
    """变焦后景深变浅，没对上焦的图送进二级模型只是浪费一次复核预算。"""
    assert rig.wait(lambda: rig.ptz.status().at_target, 3.0)
    h = rig.ptz.set_pose(0.0, 0.0, 2.4, PTZSpeed.NORMAL)
    saw_focusing = rig.wait(
        lambda: rig.ptz.status().focus_state is FocusState.FOCUSING, 2.0)
    assert saw_focusing, "变焦期间应当报 FOCUSING"
    assert rig.wait(lambda: rig.ptz.poll(h).progress is ExecProgress.DONE, 6.0)
    assert rig.ptz.status().focus_state is FocusState.LOCKED


def test_focus_failure_surfaces_as_failed(rig):
    assert rig.wait(lambda: rig.ptz.status() is not None)
    h = rig.ptz.set_pose(0.0, 0.0, 2.0, PTZSpeed.NORMAL)
    # 等假云台真的收到这条指令（它收到后会先置 FOCUSING），再注入失败——
    # 否则 FAILED 会被随后到达的指令覆盖成 FOCUSING
    assert rig.wait(lambda: rig.gimbal.t_zoom == pytest.approx(2.0), 2.0)
    rig.gimbal.force_focus_fail = True
    assert rig.wait(lambda: rig.ptz.poll(h).progress is ExecProgress.FAILED, 3.0)
    assert rig.ptz.poll(h).fail_reason == "FOCUS_FAILED"


def test_unknown_handle_fails_rather_than_hanging(rig):
    from patrol.drivers.base import ExecHandle
    r = rig.ptz.poll(ExecHandle("ptz-set_pose-ffff", 0))
    assert r.progress is ExecProgress.FAILED


# ---------------------------------------------------------------- 速率
def test_rate_command_moves_then_self_expires(rig):
    """ttl_ms 是安全兜底：**归零由云台固件自己执行**，不依赖上位机。

    没有它，mission 崩溃时云台会一直转到限位。
    """
    assert rig.wait(lambda: rig.ptz.status().at_target, 3.0)
    p0 = rig.ptz.status().pan_deg
    rig.ptz.set_rate(30.0, 0.0, ttl_ms=250)
    assert rig.wait(lambda: rig.ptz.status().pan_deg > p0 + 3.0, 2.0), "速率指令没生效"
    time.sleep(0.6)
    p1 = rig.ptz.status().pan_deg
    time.sleep(0.4)
    assert abs(rig.ptz.status().pan_deg - p1) < 0.5, "ttl 到期后云台还在转"


def test_rate_is_clamped_to_capability(rig):
    assert rig.wait(lambda: rig.ptz.status() is not None)
    rig.ptz.set_rate(1e6, 1e6, ttl_ms=3000)      # ttl 给长，别在读之前就失效
    assert rig.wait(lambda: rig.gimbal.rate is not None, 2.0)
    assert abs(rig.gimbal.rate[0]) <= rig.ptz.capabilities().max_pan_dps + 1e-6
    assert abs(rig.gimbal.rate[1]) <= rig.ptz.capabilities().max_tilt_dps + 1e-6


def test_home_returns_to_origin(rig):
    assert rig.wait(lambda: rig.ptz.status() is not None)
    rig.ptz.set_pose(60.0, 10.0, 1.0, PTZSpeed.NORMAL)
    assert rig.wait(lambda: rig.ptz.status().pan_deg > 50.0, 4.0)
    h = rig.ptz.home()
    assert rig.wait(lambda: rig.ptz.poll(h).progress is ExecProgress.DONE, 6.0)
    st = rig.ptz.status()
    assert abs(st.pan_deg) < 0.5 and abs(st.tilt_deg) < 0.5 and st.zoom == pytest.approx(1.0)


# ---------------------------------------------------------------- 限位
@pytest.mark.parametrize("pan,tilt,zoom", [
    (200.0, 0.0, 1.0),      # pan 超机械限位
    (-171.0, 0.0, 1.0),
    (0.0, 70.0, 1.0),       # tilt 超限位
    (0.0, -31.0, 1.0),
    (0.0, 0.0, 3.5),        # 变焦超光学上限
    (0.0, 0.0, 0.9),
])
def test_out_of_range_raises_instead_of_clamping(rig, pan, tilt, zoom):
    """驱动层按**硬件能力**校验，越界抛异常不截断（ICD §8.1 第二条）。

    截断会让上层的 bug 静默通过：发了 200° 被截成 170° 照常执行，联调时
    看不出问题。
    """
    with pytest.raises(ParamOutOfRange):
        rig.ptz.set_pose(pan, tilt, zoom, PTZSpeed.NORMAL)


def test_stale_pose_reports_moving_and_not_at_target(rig):
    """位姿过期不能当新鲜的用。状态机等的是 at_target，拿过期数据说"到位了"
    会让 CAPTURE 拍到糊图。"""
    assert rig.wait(lambda: rig.ptz.status().at_target, 3.0)
    rig._stop.set()                                           # noqa: SLF001
    rig._t.join(timeout=2)                                    # noqa: SLF001
    time.sleep(rig.ptz.status_timeout_s + 0.25)
    st = rig.ptz.status()
    assert st.moving is True and st.at_target is False


def test_rate_capability_zero_falls_back_instead_of_crashing(tmp_path):
    """不支持速率的云台声明 max_pan_dps = 0，mission 据此退回开环模式。

    抛 NotImplementedError 而不是发一条云台听不懂的指令。
    """
    cfg = Config.load(overrides={
        "logging": {"dir": str(tmp_path)},
        "real": {"serial": {"ptz": {"max_pan_dps": 0.0, "max_tilt_dps": 0.0}}}})
    loop = LoopbackLink()
    ptz = PTZSerial(cfg, link=loop.side_a())
    try:
        assert ptz.capabilities().max_pan_dps == 0.0
        with pytest.raises(NotImplementedError):
            ptz.set_rate(10.0, 0.0, 300)
    finally:
        ptz.close()
