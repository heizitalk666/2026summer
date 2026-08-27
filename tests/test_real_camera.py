"""相机真机驱动：V4L2 / OpenCV。

没有真相机也能测：把 `cv2.VideoCapture` 换成一个受控的假货，验的是**驱动
自己的那几条纪律**，而不是 OpenCV。这几条写错了不会崩，只会让下游所有
指标悄悄失真：

- 时间戳取曝光开始时刻，不是取回时刻 —— 取错了 latency 观测点恒等于零
- 分辨率协商失败必须报错，不能默默用 1280 —— 像素密度公式里的 W 就是它
- 连拍不足 n 帧抛错，不静默补齐
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from patrol.common.config import Config
from patrol.common.errors import DriverError, DriverNotReady
from patrol.drivers.real.camera_v4l2 import CameraV4L2


class FakeCapture:
    """够用的 VideoCapture 替身。分辨率协商与读帧失败都能被摆布。"""

    def __init__(self, *, w=1920, h=1080, opened=True, read_ok=True,
                 read_delay_s=0.0):
        self._w, self._h = w, h
        self._opened = opened
        self.read_ok = read_ok
        self.read_delay_s = read_delay_s
        self.props: dict = {}
        self.released = False
        self.reads = 0

    def isOpened(self):                                     # noqa: N802
        return self._opened

    def set(self, prop, value):                             # noqa: A003
        self.props[prop] = value
        return True

    def get(self, prop):
        import cv2
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._w)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._h)
        return float(self.props.get(prop, 0))

    def read(self):
        self.reads += 1
        if self.read_delay_s:
            time.sleep(self.read_delay_s)
        if not self.read_ok:
            return False, None
        img = np.full((self._h, self._w, 3), 40, np.uint8)
        img[0, 0] = self.reads % 256                        # 每帧不同，便于区分
        return True, img

    def release(self):
        self.released = True


@pytest.fixture()
def cam_factory(monkeypatch, tmp_path):
    """返回 (建相机, 拿到最后一个 FakeCapture)。"""
    made: list[FakeCapture] = []

    def build(**cap_kw):
        import cv2
        monkeypatch.setattr(
            cv2, "VideoCapture",
            lambda *a, **k: made.append(FakeCapture(**cap_kw)) or made[-1])
        cfg = Config.load(overrides={"logging": {"dir": str(tmp_path)}})
        return CameraV4L2(cfg), made

    return build


# ---------------------------------------------------------------- 生命周期
def test_grab_before_start_raises(cam_factory):
    cam, _ = cam_factory()
    with pytest.raises(DriverNotReady):
        cam.grab()


def test_cannot_open_device_raises_not_ready(cam_factory):
    cam, _ = cam_factory(opened=False)
    with pytest.raises(DriverNotReady):
        cam.start(1920, 1080, 30)


def test_resolution_negotiation_failure_is_loud(cam_factory):
    """**协商不到 1920×1080 必须报错，不能默默接受 1280×720。**

    像素密度公式 p = W·D·z/(2d·tan(θ₀/2)) 里的 W 就是这个数。悄悄用 1280
    会让所有 p 值系统性偏小三分之一，"够不够 120 px"的判据整体失准，而且
    一路上不会有任何报错。
    """
    cam, _ = cam_factory(w=1280, h=720)
    with pytest.raises(DriverNotReady) as e:
        cam.start(1920, 1080, 30)
    assert "1280" in str(e.value) and "1920" in str(e.value)


def test_buffer_size_is_forced_to_one(cam_factory):
    """攒帧会让"最新一帧"其实是几百毫秒前的，复核抓拍时拍到云台还没停稳的画面。"""
    import cv2
    cam, made = cam_factory()
    cam.start(1920, 1080, 30)
    assert made[-1].props.get(cv2.CAP_PROP_BUFFERSIZE) == 1


def test_stop_releases_and_start_can_be_called_again(cam_factory):
    cam, made = cam_factory()
    cam.start(1920, 1080, 30)
    cam.grab()
    cam.stop()
    assert made[-1].released
    with pytest.raises(DriverNotReady):
        cam.grab()


# ---------------------------------------------------------------- 取帧
def test_frame_shape_and_seq(cam_factory):
    cam, _ = cam_factory()
    cam.start(1920, 1080, 30)
    a, b = cam.grab(), cam.grab()
    assert (a.width, a.height) == (1920, 1080)
    assert a.image.shape == (1080, 1920, 3)
    assert b.seq == a.seq + 1, "seq 必须单调递增，丢帧靠它发现"


def test_timestamp_is_exposure_start_not_return_time(cam_factory):
    """**时间戳取曝光开始时刻，不是取回时刻。**

    取回时刻会把解码耗时算进去，而 latency_ms.capture_to_infer 要观测的
    正是这段延迟——用取回时刻它会恒等于零，这个观测点就废了。

    这里让假相机 read() 阻塞 80 ms，然后检查时间戳落在调用**之前**。
    """
    cam, _ = cam_factory(read_delay_s=0.08)
    cam.start(1920, 1080, 30)
    from patrol.common.clock import mono_ns
    t_before = mono_ns()
    fr = cam.grab()
    t_after = mono_ns()
    decode_ms = (t_after - t_before) / 1e6
    stamp_ms = (fr.ts_mono_ns - t_before) / 1e6
    assert decode_ms > 60, "假相机没有真的阻塞，这条测不出东西"
    assert stamp_ms < 10, (
        "时间戳看起来取的是取回时刻：解码用了 %.0f ms，而时间戳落在调用后 %.0f ms"
        % (decode_ms, stamp_ms))


def test_read_failure_raises_driver_error(cam_factory):
    cam, _ = cam_factory(read_ok=False)
    cam.start(1920, 1080, 30)
    with pytest.raises(DriverError):
        cam.grab()


def test_burst_returns_n_distinct_frames(cam_factory):
    """连拍 3 帧必须是**不同的** 3 帧，不能把同一帧复制三份。

    云台停稳后仍有残余抖动，3 帧里挑最清晰的一帧才有意义；复制三份的话
    这一步就是纯浪费 0.6 s。
    """
    cam, _ = cam_factory()
    cam.start(1920, 1080, 30)
    frames = cam.grab_burst(3, 20)
    assert len(frames) == 3
    assert len({f.seq for f in frames}) == 3
    assert len({int(f.image[0, 0, 0]) for f in frames}) == 3


def test_burst_propagates_failure_instead_of_short_returning(cam_factory):
    """不足 n 帧要抛错，不能静默返回 2 帧——下游按 3 帧写的。"""
    cam, made = cam_factory()
    cam.start(1920, 1080, 30)
    made[-1].read_ok = False
    with pytest.raises(DriverError):
        cam.grab_burst(3, 10)


def test_burst_interval_is_respected(cam_factory):
    cam, _ = cam_factory()
    cam.start(1920, 1080, 30)
    t0 = time.monotonic()
    cam.grab_burst(3, 60)
    dt = time.monotonic() - t0
    assert dt >= 0.11, "连拍间隔没生效，实测只用了 %.0f ms" % (dt * 1000)


def test_capabilities_reflect_negotiated_size(cam_factory):
    cam, _ = cam_factory()
    assert cam.capabilities().width == 1920
    cam.start(1920, 1080, 30)
    caps = cam.capabilities()
    assert (caps.width, caps.height) == (1920, 1080)
