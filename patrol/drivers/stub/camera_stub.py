"""相机桩。ICD §8.5 / §9.2。

从虚拟配电室渲染取帧。它需要知道车在哪、云台指向哪，所以持有 chassis 与
ptz 的引用——这在真机上是不存在的耦合（真相机不知道车在哪），但桩本来就是
在"扮演"整个物理世界，这层耦合封在 stub 包内，不会泄漏到业务代码。

**但持有引用还不够。**系统跑起来是四个进程，每个进程各建一套驱动，只有网关
那一套会收到指令（它是唯一能碰执行器的进程）。感知进程里的 ptz 永远停在开机
位 (0,0,1×)，据此渲染出的画面与 IF-3 报的状态说的是两个世界。所以视点可以由
外部通过 ``observe_state()`` 接管，感知节点每收到一条 IF-3 就喂一次。

两条与真机对齐的语义（ICD §9.4 一致性要求）：

1. ``Frame.ts_mono_ns`` 取**曝光开始时刻**，不是取回时刻。取回时刻会把渲染
   /解码延迟算进去，而 latency_ms.capture_to_infer 要观测的正是这段延迟，
   用取回时刻会让它恒等于零，观测点就废了。
2. ``grab_burst`` 保证 n 帧之间没有丢帧，不足 n 帧抛 DriverError。
"""
from __future__ import annotations

import threading

import numpy as np

from patrol.common.clock import mono_ns, utc_ms
from patrol.common.errors import DriverError, DriverNotReady
from patrol.drivers.base import CameraCaps, Frame, ICamera
from patrol.drivers.stub.chassis_stub import ChassisStub
from patrol.drivers.stub.ptz_stub import PTZStub
from patrol.scene.render import RenderOptions, SceneRenderer
from patrol.scene.world import World


class CameraStub(ICamera):
    def __init__(self, cfg, world: World, chassis: ChassisStub, ptz: PTZStub,
                 localizer=None, seed: int = 0):
        cam = cfg.get("camera")
        stub = cfg.get("stub.camera", {})
        self.cfg, self.world = cfg, world
        self._chassis, self._ptz, self._loc = chassis, ptz, localizer
        self._caps = CameraCaps(
            width=int(cam.get("width", 1920)), height=int(cam.get("height", 1080)),
            max_fps=int(stub.get("max_fps", cam.get("fps", 30))),
            pixel_format=str(cam.get("pixel_format", "BGR888")),
        )
        self._lat_ms = tuple(stub.get("grab_latency_ms", [8, 18]))
        self.rng = np.random.default_rng(seed)
        self._renderer = SceneRenderer(world, RenderOptions(
            width=self._caps.width, height=self._caps.height,
            hfov_at_1x_deg=float(cfg.get("optics.hfov_at_1x_deg", 60.0)),
            simulate_4k_crop=bool(cfg.get("stub.ptz.simulate_4k_crop", True)),
            source_width=int(cfg.get("stub.ptz.source_width", 3840)),
        ), seed=seed)
        self._started = False
        self._seq = 0
        self._lock = threading.Lock()
        self._last_meta: list[dict] = []
        self._ext: tuple | None = None          # 外部视点（来自 IF-3）
        self._ext_ns = 0
        self._ext_ttl_ns = int(float(stub.get("viewpoint_ttl_ms", 500)) * 1e6)

    # ------------------------------------------------------------ 取景
    def observe_state(self, *, pose_xy_yaw, pan_deg: float, tilt_deg: float,
                      zoom: float, speed_mps: float) -> None:
        """接管视点。见 ICamera.observe_state 的说明。

        带 TTL 是为了让**同一个类**在两种场合都对：跑全系统时感知进程持续喂
        IF-3，视点始终新鲜；而 tools/viewer.py、tools/calibrate.py 和单元测试
        自己持有整套驱动、根本没有 IF-3，喂不进来就自动退回本地驱动。
        """
        with self._lock:
            self._ext = (tuple(float(v) for v in pose_xy_yaw), float(pan_deg),
                         float(tilt_deg), float(zoom), float(speed_mps))
            self._ext_ns = mono_ns()

    def _viewpoint(self) -> tuple[tuple[float, float, float], float, float, float, float]:
        """当前视点：(车位姿, pan, tilt, zoom, 车速)。"""
        with self._lock:
            if self._ext is not None and mono_ns() - self._ext_ns <= self._ext_ttl_ns:
                return self._ext
        if self._loc is not None:
            p = self._loc.get_pose()
            pose_xy_yaw = (p.x_m, p.y_m, p.yaw_deg)
        else:
            st = self._chassis.status()
            x, y, yaw, _ = self.world.pose_at(self._chassis.travelled_m())
            pose_xy_yaw = (x, y, yaw)
        pan, tilt, zoom = self._ptz.true_pose()
        return pose_xy_yaw, pan, tilt, zoom, self._chassis.status().speed_mps

    def _render_frame(self) -> Frame:
        # 曝光开始时刻先取，渲染耗时才算进 capture_to_infer
        ts_mono, ts_utc = mono_ns(), utc_ms()
        pose, pan, tilt, zoom, spd = self._viewpoint()
        img, meta = self._renderer.render(pose_xy_yaw=pose, pan_deg=pan,
                                          tilt_deg=tilt, zoom=zoom, speed_mps=spd)
        with self._lock:
            self._seq += 1
            seq = self._seq
            self._last_meta = meta
        return Frame(seq=seq, ts_mono_ns=ts_mono, ts_utc_ms=ts_utc,
                     image=img, width=img.shape[1], height=img.shape[0])

    # ------------------------------------------------------------ ICamera
    def capabilities(self) -> CameraCaps:
        return self._caps

    def start(self, width: int, height: int, fps: int) -> None:
        if width != self._caps.width or height != self._caps.height:
            self._caps = CameraCaps(width, height, self._caps.max_fps,
                                    self._caps.pixel_format)
            self._renderer.o.width, self._renderer.o.height = width, height
        self._started = True

    def grab(self, timeout_ms: int = 200) -> Frame:
        if not self._started:
            raise DriverNotReady("相机未 start()")
        return self._render_frame()

    def grab_burst(self, n: int, interval_ms: int) -> list[Frame]:
        """连拍。CAPTURE 状态用 n=3, interval_ms=150。

        每帧之间云台的残余抖动不同（ptz_stub 的 settle_jitter_deg），所以
        3 帧里挑最清晰的一帧确实有意义——这不是摆设。
        """
        if not self._started:
            raise DriverNotReady("相机未 start()")
        import time
        out: list[Frame] = []
        for i in range(int(n)):
            if i:
                time.sleep(max(0.0, interval_ms / 1000.0))
            out.append(self._render_frame())
        if len(out) != int(n):
            raise DriverError("连拍不足 %d 帧，实得 %d 帧" % (n, len(out)))
        return out

    def stop(self) -> None:
        self._started = False

    def close(self) -> None:
        self._started = False

    # ------------------------------------------------------------ 测试用
    def last_targets(self) -> list[dict]:
        """上一帧里目标的真值元数据。

        **只给桩内部的合成检测器与测试用。**感知节点通过 ICamera 只能拿到
        Frame，拿不到这个——读数算法必须真的从像素里解算，见 scene/gauges.py
        的纪律说明。
        """
        with self._lock:
            return list(self._last_meta)
