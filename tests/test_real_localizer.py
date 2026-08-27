"""定位真机驱动：从底盘状态帧推位姿。

这个模块此前覆盖率也是 0。它比看上去容易写错，因为**位置不是测出来的，
是按"路线完成比例"沿标定折线插出来的**——航位推算。插值端点取错、闭环
没接上、或者误报成 LIDAR_SLAM，都不会崩，只会给出一个看起来很正常的错位姿。
"""
from __future__ import annotations

import math
import time

import pytest

from patrol.common.clock import mono_ns
from patrol.common.config import Config
from patrol.common.errors import DriverNotReady
from patrol.drivers.base import ChassisState, ChassisStatus, PoseSource
from patrol.drivers.real.localizer_serial import LocalizerSerial


class _FakeChassis:
    """只需要 status()。progress 由测试直接摆布。"""

    def __init__(self, progress=0.0, state=ChassisState.MOVING, ready=True):
        self.progress = progress
        self.state = state
        self.ready = ready

    def status(self):
        if not self.ready:
            raise DriverNotReady("还没收到状态帧")
        return ChassisStatus(
            state=self.state, speed_mps=0.25, path_progress=self.progress,
            distance_to_goal_m=None, current_waypoint_id="WP-01",
            battery_pct=90.0, safety_layer_active=False, ts_mono_ns=mono_ns())


def make(tmp_path, chassis, **overrides):
    base = {"logging": {"dir": str(tmp_path)},
            "real": {"localizer": {"rate_hz": 100.0}}}
    for k, v in overrides.items():
        base["real"]["localizer"][k] = v
    return LocalizerSerial(Config.load(overrides=base), chassis)


def wait_pose(loc, timeout_s=2.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        try:
            return loc.get_pose()
        except DriverNotReady:
            time.sleep(0.01)
    raise AssertionError("2 s 内没解出位姿")


def test_pose_before_any_status_raises(tmp_path):
    """底盘还没上报就问位姿，必须报 DriverNotReady，不能返回 (0,0,0)。

    编一个原点出来最危险：GOTO_OBSERVE 会照着这个假位姿去规划。
    """
    loc = make(tmp_path, _FakeChassis(ready=False))
    try:
        with pytest.raises(DriverNotReady):
            loc.get_pose()
    finally:
        loc.close()


def test_progress_zero_is_the_first_waypoint(tmp_path):
    ch = _FakeChassis(progress=0.0)
    loc = make(tmp_path, ch)
    try:
        p = wait_pose(loc)
        assert (p.x_m, p.y_m) == pytest.approx((0.50, -3.18), abs=0.02)
    finally:
        loc.close()


def test_pose_advances_monotonically_along_the_route(tmp_path):
    """完成比例递增，位置必须沿路线前进，不能来回跳。"""
    ch = _FakeChassis(progress=0.0)
    loc = make(tmp_path, ch)
    try:
        wait_pose(loc)
        seen = []
        for pr in (0.0, 0.1, 0.2, 0.3, 0.4):
            ch.progress = pr
            time.sleep(0.05)
            p = loc.get_pose()
            seen.append((round(p.x_m, 2), round(p.y_m, 2)))
        xs = [x for x, _ in seen]
        assert xs == sorted(xs), "位置没有沿路线单调前进：%s" % seen
        assert all(abs(y + 3.18) < 0.05 for _, y in seen), \
            "巡检位全在 y=-3.18 的过道上，插值不该把 y 带偏：%s" % seen
    finally:
        loc.close()


def test_progress_is_clamped_not_wrapped(tmp_path):
    """progress 超出 [0,1] 时夹住，不是取模。

    取模会让 1.02 变成 0.02——车明明在终点，位姿却报回起点，
    WAYPOINT_ONCE 抑制立刻全部失效。
    """
    ch = _FakeChassis(progress=1.0)
    loc = make(tmp_path, ch)
    try:
        end = wait_pose(loc)
        ch.progress = 1.5
        time.sleep(0.05)
        over = loc.get_pose()
        assert (over.x_m, over.y_m) == pytest.approx((end.x_m, end.y_m), abs=1e-6)
        ch.progress = -0.5
        time.sleep(0.05)
        under = loc.get_pose()
        assert under.x_m == pytest.approx(0.50, abs=0.02)
    finally:
        loc.close()


def test_yaw_follows_the_current_leg(tmp_path):
    """yaw 取当前路段方向。过道是东西向，所以只会是 0° 或 ±180°。"""
    ch = _FakeChassis(progress=0.05)
    loc = make(tmp_path, ch)
    try:
        wait_pose(loc)
        yaws = []
        for pr in (0.05, 0.25, 0.5, 0.75, 0.95):
            ch.progress = pr
            time.sleep(0.05)
            yaws.append(round(loc.get_pose().yaw_deg, 1))
        assert all(abs(y) < 1.0 or abs(abs(y) - 180.0) < 1.0 for y in yaws), \
            "过道是东西向的，yaw 只该是 0 或 ±180：%s" % yaws
    finally:
        loc.close()


def test_source_is_always_odom_only(tmp_path):
    """**车上没有激光雷达，就不许报 LIDAR_SLAM。**

    这个字段会原样进证据包。将来查一次定位偏差时，它是判断"该信多少"的
    唯一依据；报错了等于把排查引到沟里。
    """
    ch = _FakeChassis(progress=0.3)
    loc = make(tmp_path, ch)
    try:
        assert wait_pose(loc).source is PoseSource.ODOM_ONLY
        ch.state = ChassisState.FAULT
        time.sleep(0.05)
        assert loc.get_pose().source is PoseSource.ODOM_ONLY
    finally:
        loc.close()


@pytest.mark.parametrize("state,valid", [
    (ChassisState.MOVING, True), (ChassisState.STOPPED, True),
    (ChassisState.FAULT, False), (ChassisState.ESTOP, False),
])
def test_chassis_health_gates_pose_validity(tmp_path, state, valid):
    """FAULT / ESTOP 时 valid=false，触发 POSE_INVALID 抑制。

    宁可不复核，也不在漂移的坐标系里下发 GOTO_OBSERVE。
    """
    ch = _FakeChassis(progress=0.2, state=state)
    loc = make(tmp_path, ch)
    try:
        p = wait_pose(loc)
        assert p.valid is valid
        assert (p.cov_trace > 0.1) is (not valid), \
            "失效时协方差迹要抬起来，mission 据此判断定位是否可信"
    finally:
        loc.close()


def test_route_does_not_come_from_the_virtual_scene(tmp_path):
    """**真机模式不许读 scene.\\***。

    原来这里写的是 cfg.get("scene.route.points")，而 system.yaml 无条件
    include 了 scene.yaml——真车跑起来会拿虚拟配电室的巡检顺序去插值真车的
    位置，错得毫无征兆。这条用例给 scene 塞一条完全不同的路线，位姿必须不受影响。
    """
    def pose_with(scene_route):
        ov = {"logging": {"dir": str(tmp_path)},
              "real": {"localizer": {"rate_hz": 100.0}}}
        if scene_route is not None:
            ov["scene"] = {"route": {"points": scene_route}}
        loc = LocalizerSerial(Config.load(overrides=ov), _FakeChassis(progress=0.5))
        try:
            p = wait_pose(loc)
            return round(p.x_m, 4), round(p.y_m, 4), round(p.yaw_deg, 4)
        finally:
            loc.close()

    clean = pose_with(None)
    # 两条截然不同的投毒路线，位姿都必须与不投毒时**逐位相同**
    assert pose_with(["WP-06", "WP-05"]) == clean, "位姿被 scene.route 影响了"
    assert pose_with(["WP-01", "WP-02"]) == clean, "位姿被 scene.route 影响了"


def test_explicit_route_points_are_honoured(tmp_path):
    """真机路线可以显式给，缺省才按标定表顺序。"""
    ch = _FakeChassis(progress=0.0)
    loc = make(tmp_path, ch, route_points=["WP-06", "WP-05"])
    try:
        p = wait_pose(loc)
        assert p.x_m == pytest.approx(16.00, abs=0.02)     # WP-06
    finally:
        loc.close()


def test_subscribers_get_called(tmp_path):
    ch = _FakeChassis(progress=0.1)
    loc = make(tmp_path, ch)
    got = []
    loc.subscribe(got.append)
    try:
        wait_pose(loc)
        t0 = time.monotonic()
        while not got and time.monotonic() - t0 < 1.0:
            time.sleep(0.02)
        assert got, "注册的位姿回调没有被调用"
    finally:
        loc.close()


def test_a_throwing_subscriber_does_not_kill_the_loop(tmp_path):
    """一个回调抛异常不能把整条定位线程带走。"""
    ch = _FakeChassis(progress=0.1)
    loc = make(tmp_path, ch)
    good = []
    loc.subscribe(lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    loc.subscribe(good.append)
    try:
        wait_pose(loc)
        t0 = time.monotonic()
        while len(good) < 3 and time.monotonic() - t0 < 2.0:
            time.sleep(0.02)
        assert len(good) >= 3, "前一个回调抛异常之后，后面的就不再被调用了"
    finally:
        loc.close()
