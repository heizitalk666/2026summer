"""驱动注入。ICD §8.7。

``driver_mode`` 是配置文件里**唯一**区分桩和真机的开关。除本文件外，任何
其他文件出现 ``if mode == "stub"`` 都算违反本约定，评审时会检查
（ICD §10.2 checklist：「除 factory.py 外全仓库搜不到 if mode == "stub"」）。

桩和真机的差异必须全部封在实现类里。一旦泄漏到业务代码，"桩环境验证过的
逻辑在真机上同样成立"这个前提就不成立了，而整个无硬件并行开发方案就是
建立在这个前提上的。
"""
from __future__ import annotations

from patrol.drivers.base import IChassis, ICamera, ILocalizer, IPTZ


def build_drivers(cfg, *, seed: int = 0
                  ) -> tuple[IChassis, IPTZ, ICamera, ILocalizer]:
    mode = str(cfg.get("driver_mode", "stub")).lower()
    if mode == "stub":
        return _build_stub(cfg, seed=seed)
    if mode == "real":
        return _build_real(cfg)
    raise ValueError("driver_mode 只能是 stub 或 real，收到 %r" % mode)


def _build_stub(cfg, *, seed: int):
    from patrol.drivers.stub.camera_stub import CameraStub
    from patrol.drivers.stub.chassis_stub import ChassisStub
    from patrol.drivers.stub.pose_stub import PoseStub
    from patrol.drivers.stub.ptz_stub import PTZStub
    from patrol.scene.world import World

    world = World(cfg)
    chassis = ChassisStub(cfg, world, seed=seed)
    ptz = PTZStub(cfg, seed=seed + 1)
    localizer = PoseStub(cfg, world, chassis, seed=seed + 2)
    camera = CameraStub(cfg, world, chassis, ptz, localizer, seed=seed + 3)
    return chassis, ptz, camera, localizer


def _build_real(cfg):
    from patrol.drivers.real.camera_v4l2 import CameraV4L2
    from patrol.drivers.real.chassis_serial import ChassisSerial
    from patrol.drivers.real.localizer_serial import LocalizerSerial
    from patrol.drivers.real.ptz_serial import PTZSerial

    link = cfg.get("real.serial", {})
    chassis = ChassisSerial(cfg)
    ptz = PTZSerial(cfg)
    camera = CameraV4L2(cfg)
    localizer = LocalizerSerial(cfg, chassis)
    return chassis, ptz, camera, localizer
