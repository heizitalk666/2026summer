"""真机相机没有渲染器的 world，读数先验仍要能从标定表（scene 配置里的目标清单）按投影匹配取到。"""
from __future__ import annotations

from types import SimpleNamespace

from patrol.common.config import Config
from patrol.perception.detector.base import Detection
from patrol.perception.node import PerceptionNode
from patrol.scene.optics import PinholeCamera, hfov_at_zoom
from patrol.scene.world import World

W_PX, H_PX = 1920, 1080


def _bare_node(cfg):
    """只带 _priors_for 用得到的属性，不起总线、不开驱动。"""
    n = PerceptionNode.__new__(PerceptionNode)
    n.cfg = cfg
    n.hfov1x = float(cfg.get("optics.hfov_at_1x_deg", 60.0))
    n.camera = SimpleNamespace()          # 真机相机：没有 world
    return n


def test_real_camera_gets_priors_from_calibration_table(tmp_path):
    cfg = Config.load(overrides={"logging": {"dir": str(tmp_path)}})
    world = World(cfg)
    target = next(t for t in world.targets if t.defect_class == "PRESSURE_GAUGE")
    wp = world.waypoints[target.waypoint]
    zoom, tilt = 1.0, 0.0
    # 在该目标的巡检位上转云台，找到目标落在画面里的那个方位
    hit = None
    for pan in range(-180, 181, 2):
        cam = PinholeCamera(W_PX, H_PX, hfov_at_zoom(60.0, zoom), (wp.x_m, wp.y_m, world.camera_height_m),
                            0.0, float(pan), tilt)
        for t, uv in world.visible(cam, margin_px=0.0):
            if t.id == target.id and uv[:, 0].min() > 0 and uv[:, 0].max() < W_PX                     and uv[:, 1].min() > 0 and uv[:, 1].max() < H_PX:
                hit = (pan, uv)
                break
        if hit:
            break
    assert hit, "测试前提不成立：巡检位上找不到能看全目标的云台方位"
    pan, uv = hit
    det = Detection("PRESSURE_GAUGE", 0.9, (float(uv[:, 0].min()), float(uv[:, 1].min()),
                                            float(uv[:, 0].max()), float(uv[:, 1].max())))
    node = _bare_node(cfg)
    node._last_status = {"pose": {"x_m": wp.x_m, "y_m": wp.y_m, "yaw_deg": 0.0},
                         "ptz": {"zoom": zoom, "pan_deg": float(pan), "tilt_deg": tilt}}
    frame = SimpleNamespace(width=W_PX, height=H_PX)
    priors = node._priors_for(det, frame)
    assert priors is not None, "真机相机上读数先验取不到"
    assert priors == target.priors
