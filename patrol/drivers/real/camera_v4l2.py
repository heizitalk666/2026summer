"""相机真机驱动：V4L2 / OpenCV。

RK3576 上最终应当走 MPP 硬解 + 零拷贝，但那要等板子到手才能调。这一版用
OpenCV 的 V4L2 后端，接口语义与最终实现一致，换硬解只改本文件。

两条**必须**与桩对齐的语义（ICD §9.4），写错了下游指标会集体失真：

1. ``Frame.ts_mono_ns`` 取**曝光开始时刻**，不是取回时刻。用取回时刻会让
   ``latency_ms.capture_to_infer`` 恒等于零，那个观测点就废了。这里的做法是
   在 ``grab()`` 之前打时间戳——`VideoCapture.read()` 内部含解码，把它算进
   延迟才是对的。
2. ``grab_burst`` 保证 n 帧之间**没有丢帧**。OpenCV 的抓取缓冲会攒帧，所以
   把 `CAP_PROP_BUFFERSIZE` 设成 1，读到的永远是最新一帧；不足 n 帧抛
   DriverError，不静默补齐。
"""
from __future__ import annotations

import time

import numpy as np

from patrol.common.clock import mono_ns, utc_ms
from patrol.common.errors import DriverError, DriverNotReady
from patrol.drivers.base import CameraCaps, Frame, ICamera


class CameraV4L2(ICamera):
    def __init__(self, cfg):
        c = dict(cfg.get("real.camera", {}))
        cam = cfg.get("camera")
        self.device = c.get("device", 0)
        self.fourcc = str(c.get("fourcc", "MJPG"))
        self._caps = CameraCaps(
            width=int(cam.get("width", 1920)), height=int(cam.get("height", 1080)),
            max_fps=int(c.get("max_fps", cam.get("fps", 30))),
            pixel_format=str(cam.get("pixel_format", "BGR888")))
        self._cap = None
        self._seq = 0
        self._grab_timeout_s = float(c.get("grab_timeout_s", 0.5))

    # ------------------------------------------------------------
    def capabilities(self) -> CameraCaps:
        return self._caps

    def start(self, width: int, height: int, fps: int) -> None:
        import cv2
        dev = int(self.device) if str(self.device).isdigit() else self.device
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise DriverNotReady("打不开相机 %r" % self.device)
        if self.fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
        cap.set(cv2.CAP_PROP_FPS, int(fps))
        # 缓冲设成 1：攒帧会让"最新一帧"其实是几百毫秒前的，复核抓拍时
        # 拍到的是云台还没停稳时的画面
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        got_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or width)
        got_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or height)
        if (got_w, got_h) != (int(width), int(height)):
            # 分辨率协商失败必须让它可见：像素密度公式里的 W 就是这个数，
            # 悄悄用 1280 会让所有 p 值系统性偏小三分之一
            raise DriverNotReady("相机不支持 %dx%d，实际协商到 %dx%d"
                                 % (width, height, got_w, got_h))
        self._caps = CameraCaps(got_w, got_h, self._caps.max_fps,
                                self._caps.pixel_format)
        self._cap = cap

    def grab(self, timeout_ms: int = 200) -> Frame:
        if self._cap is None:
            raise DriverNotReady("相机未 start()")
        ts_mono, ts_utc = mono_ns(), utc_ms()     # 曝光开始时刻，见模块文档
        ok, img = self._cap.read()
        if not ok or img is None:
            raise DriverError("取帧失败")
        self._seq += 1
        return Frame(seq=self._seq, ts_mono_ns=ts_mono, ts_utc_ms=ts_utc,
                     image=np.ascontiguousarray(img),
                     width=img.shape[1], height=img.shape[0])

    def grab_burst(self, n: int, interval_ms: int) -> list[Frame]:
        out: list[Frame] = []
        for i in range(int(n)):
            if i:
                time.sleep(max(0.0, interval_ms / 1000.0))
            out.append(self.grab())
        if len(out) != int(n):
            raise DriverError("连拍不足 %d 帧，实得 %d 帧" % (n, len(out)))
        return out

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def close(self) -> None:
        self.stop()
