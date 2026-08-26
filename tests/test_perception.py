"""感知层：报文合规、触发判据、跟踪连续性、质量指标、L3。"""
from __future__ import annotations

import time

import numpy as np
import pytest

from patrol.common import messages as M
from patrol.common.config import Config
from patrol.drivers.base import PTZSpeed
from patrol.perception.detector.base import Detection
from patrol.perception.quality import (blur_score, evaluate, highlight_ratio,
                                       occlusion_ratio)
from patrol.perception.reading.indicator import read_indicator_light
from patrol.perception.reading.switch import read_switch_position
from patrol.perception.tracker import IouTracker, iou
from patrol.scene.gauges import (add_glass_glare, render_indicator_light,
                                 render_pointer_gauge, render_switch_handle)


# ---------------------------------------------------------------- 离散状态量
@pytest.mark.parametrize("color", ["RED", "GREEN", "YELLOW", "BLUE"])
@pytest.mark.parametrize("on", [True, False])
def test_indicator_light(color, on):
    img = render_indicator_light(96, color=color, on=on)
    r = read_indicator_light(img, (0, 0, 95, 95))
    assert r.ok
    assert r.value == (color if on else "OFF")


def test_switch_position_accuracy():
    """方案书表 2-2：开关分合位正确率 ≥99 %，全系统要求最高的一项。"""
    import cv2
    rng = np.random.default_rng(0)
    ok = n = 0
    for pos in ("CLOSED", "OPEN"):
        for _ in range(40):
            sz = int(rng.integers(50, 220))
            img = render_switch_handle(sz, position=pos).astype(np.float32)
            img = np.clip(img + rng.normal(0, 10, img.shape), 0, 255).astype(np.uint8)
            Mrot = cv2.getRotationMatrix2D((sz / 2, sz / 2), float(rng.uniform(-10, 10)), 1.0)
            img = cv2.warpAffine(img, Mrot, (sz, sz), borderMode=cv2.BORDER_REPLICATE)
            n += 1
            ok += read_switch_position(img, (0, 0, sz - 1, sz - 1)).value == pos
    assert ok / n >= 0.99, "正确率 %.3f 未达 99 %%" % (ok / n)


# ---------------------------------------------------------------- 质量指标
def test_quality_metrics_discriminate():
    import cv2
    tex = render_pointer_gauge(300, value=0.42, range_min=0, range_max=1.6)
    box = (0, 0, 299, 299)
    assert blur_score(tex, box) > 0.8
    assert blur_score(cv2.GaussianBlur(tex, (15, 15), 0), box) < 0.2
    assert highlight_ratio(tex, box) < 0.02
    assert highlight_ratio(add_glass_glare(tex, strength=0.98), box) > 0.05
    assert occlusion_ratio(tex, box) == pytest.approx(0.0, abs=1e-6)
    assert occlusion_ratio(tex, (-150, 0, 149, 299)) == pytest.approx(0.5, abs=0.02)


def test_quality_score_drops_with_pixel_density(cfg):
    tex = render_pointer_gauge(300, value=0.42, range_min=0, range_max=1.6)
    qc = cfg.get("perception.quality")
    lo = evaluate(tex, (0, 0, 299, 299), target_size_m=0.15, zoom=1.0,
                  distance_m=8.0, hfov_at_1x_deg=60.0, cfg_quality=qc)
    hi = evaluate(tex, (0, 0, 299, 299), target_size_m=0.15, zoom=3.0,
                  distance_m=2.0, hfov_at_1x_deg=60.0, cfg_quality=qc)
    assert lo.score < hi.score
    assert lo.pixel_density_px < hi.pixel_density_px


# ---------------------------------------------------------------- 跟踪
def test_iou_basic():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_tracker_keeps_id_while_target_moves():
    """跟踪不断，同一目标就不会被反复当作新目标触发重复测量。"""
    t = IouTracker(iou_threshold=0.3, max_age=15)
    ids = []
    for i in range(20):
        d = Detection("PRESSURE_GAUGE", 0.5, (100.0 + i * 6, 100.0, 160.0 + i * 6, 160.0))
        ids.append(t.update([d])[0].track_id)
    assert len(set(ids)) == 1, "帧间位移 6 px、框宽 60 px 时不应换 id：%s" % ids


def test_tracker_new_id_after_long_gap():
    t = IouTracker(iou_threshold=0.3, max_age=3)
    first = t.update([Detection("PRESSURE_GAUGE", 0.5, (100, 100, 160, 160))])[0].track_id
    for _ in range(5):
        t.update([])
    second = t.update([Detection("PRESSURE_GAUGE", 0.5, (100, 100, 160, 160))])[0].track_id
    assert second != first, "断链超过 max_age 后应分配新 id（靠巡检位规则兜底）"


def test_tracker_does_not_mix_classes():
    t = IouTracker()
    a = t.update([Detection("PRESSURE_GAUGE", 0.5, (10, 10, 50, 50))])[0].track_id
    b = t.update([Detection("INDICATOR_LIGHT", 0.5, (10, 10, 50, 50))])[0].track_id
    assert a != b


# ---------------------------------------------------------------- 节点
@pytest.fixture()
def node(cfg_ports, tmp_path):
    from patrol.perception.node import PerceptionNode
    c = Config.load(overrides={"bus": cfg_ports.get("bus"),
                               "logging": {"dir": str(tmp_path), "level": "ERROR"}})
    n = PerceptionNode(c, seed=5)
    n.camera.start(1920, 1080, 10)
    cp = c.get("mission.cruise_ptz")
    n.ptz.set_pose(cp["pan_deg"], cp["tilt_deg"], cp["zoom"], PTZSpeed.NORMAL)
    t0 = time.time()
    while time.time() - t0 < 3.0 and not n.ptz.status().at_target:
        time.sleep(0.02)
    yield n
    n.close()


def test_events_pass_schema_and_meet_latency(node):
    """巡航态 total 的 P95 必须小于 100 ms，否则 10 FPS 的节拍保不住。

    ICD §3.1：这个字段是联调时唯一的性能观测点，不允许省略。

    先跑几帧预热再计时：桩的贴图缓存（真机上对应模型加载与首帧显存分配）
    只在头几帧付一次成本，实测冷启动首帧 500 ms、稳态 60 ms。P95 指标针对
    的是巡航稳态，把冷启动算进去衡量的是另一件事。
    """
    for _ in range(5):
        node.process_frame(node.camera.grab(), stage="CRUISE")
    lat = []
    for _ in range(20):
        ev = node.process_frame(node.camera.grab(), stage="CRUISE")
        M.validate(ev, "DETECTION_EVENT")
        lat.append(ev["latency_ms"]["total"])
        assert ev["latency_ms"]["capture_to_infer"] >= 0
    assert float(np.percentile(lat, 95)) < 100.0, "P95=%.0f ms" % np.percentile(lat, 95)


def test_empty_detections_still_published(node):
    """巡航态哪怕没有检出也要发报文，mission 靠它判断感知进程还活着。"""
    ev = node.process_frame(node.camera.grab(), stage="CRUISE")
    assert "detections" in ev and isinstance(ev["detections"], list)
    M.validate(ev, "DETECTION_EVENT")


def test_suspect_carries_event_id(node):
    """is_suspect = true 时 event_id 必须非空（ICD §10.1 第 9 条反例）。"""
    for _ in range(30):
        ev = node.process_frame(node.camera.grab(), stage="CRUISE")
        if ev["suspect"]["is_suspect"]:
            assert ev["event_id"] is not None
            assert ev["suspect"]["trigger_rule"] is not None
            assert 0.0 <= ev["suspect"]["priority"] <= 1.0
            return
    pytest.skip("本次运行未触发复核")


def test_conf_band_rule():
    """confidence ≥ 0.60 的检出直接判定为缺陷，不占复核预算（ICD §3.3）。"""
    from patrol.perception.node import PerceptionNode
    rule = PerceptionNode._trigger_rule
    class Fake:
        first_release = {"PRESSURE_GAUGE"}
        p_min = 120.0
        q_thr = 0.75
    d_low = Detection("PRESSURE_GAUGE", 0.41, (0, 0, 200, 200))
    d_high = Detection("PRESSURE_GAUGE", 0.85, (0, 0, 200, 200))
    assert rule(Fake(), d_low, 200.0, None, None) == "CONF_BAND"
    assert rule(Fake(), d_high, 200.0, None, None) is None
    assert rule(Fake(), d_high, 50.0, None, None) == "L2_UNREADABLE"


def test_l3_output_never_becomes_an_alarm(node):
    """ICD §3.1：L3 的输出只允许进人工复核队列，不得当作缺陷判定直接告警。

    这里验证接口层面的约束：l3_anomaly 与 detections 是分开的两块，
    is_anomaly 不会写进任何 detection 的 defect_class。
    """
    ev = node.process_frame(node.camera.grab(), stage="CRUISE")
    l3 = ev.get("l3_anomaly")
    if l3 is None:
        pytest.skip("L3 未启用")
    assert set(l3) == {"model", "anomaly_score", "threshold", "is_anomaly", "heatmap_ref"}
    for d in ev["detections"]:
        assert "anomaly" not in d.get("defect_class", "").lower()
