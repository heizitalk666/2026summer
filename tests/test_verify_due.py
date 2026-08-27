"""perception 怎么知道"现在该发复核报文了"。

**这是 ICD 的一个缺口留下的地方，也是全项目最容易悄悄失效的一处判据。**

§7.2 写着 VERIFY 阶段"走 perception"，但四条冻结接口里没有任何一条把
mission_state 送到 perception —— HEARTBEAT.params.mission_state 只从 mission
发往 gateway。所以 perception 只能看 IF-3 的状态组合去**推断**状态机走到哪了。

推断错的两个方向，代价都很重，而且都不报错：

- **偏晚**（判据太严）：状态机在 VERIFY 干等到超时，证据包变成一个
  density_ratio = 0 的 INCONCLUSIVE
- **偏早**（判据太松）：verify_due() 一命中就立刻发报文并清掉 event_id，
  于是复核报的是 ZOOM 之前的画面，密度比 ≈ 1.0 —— 等于什么都没复核，
  但看起来一切正常

两个方向这里各钉了一组。
"""
from __future__ import annotations

import pytest

from patrol.common.config import Config
from patrol.perception.node import PerceptionNode
from patrol.scene.optics import zoom_for_density


class _Log:
    def __init__(self):
        self.warns = []

    def warn(self, msg, **kw):
        self.warns.append((msg, kw))


class Node:
    """只取判据要用的那几个字段，不起真节点（起节点要开相机与总线）。"""

    def __init__(self, *, target_zoom=2.4, cruise_zoom=1.0, p_min=120.0,
                 max_zoom=3.0, bearing=90.0, tilt=2.0, tol=12.0, grace=1.5):
        self.cruise_zoom = cruise_zoom
        self.max_zoom = max_zoom
        self.p_min = p_min
        self.cruise_pans = (bearing, -bearing)
        self.cruise_tilt = tilt
        self.cruise_pose_tol_deg = tol
        self._event_target_zoom = target_zoom
        self._last_status = None
        self._verify_ready_since_ns = 0
        self.verify_grace_s = grace
        self.log = _Log()

    verify_due = PerceptionNode.verify_due
    _zoom_settled = PerceptionNode._zoom_settled
    _ptz_left_cruise_pose = PerceptionNode._ptz_left_cruise_pose
    _expected_zoom = PerceptionNode._expected_zoom


def status(*, state="STOPPED", zoom=2.4, pan=110.0, tilt=2.7,
           at_target=True, focus="LOCKED"):
    return {"chassis": {"state": state},
            "ptz": {"zoom": zoom, "pan_deg": pan, "tilt_deg": tilt,
                    "at_target": at_target, "focus_state": focus}}


# ---------------------------------------------------------------- 该发
def test_fires_when_zoom_reached_the_commanded_target():
    n = Node(target_zoom=2.4)
    n._last_status = status(zoom=2.4)
    assert n.verify_due() is True


def test_fires_at_a_modest_target_zoom():
    """**这条钉的是一个真 bug，而且是最恶劣的那种：越近越必然失败。**

    原来判据写的是"变焦 ≥ verify_zoom_min = 1.8"，可状态机下发的倍率是
    按需算的 clip(p_target / p_before, 1, max_zoom)。两个常数各自独立定，
    凑一起就锁死了：p_target = 120 时，只有巡航密度 ≤ 66.7 px 的目标才
    够得到 1.8×。**比这更近、看得更清楚的目标，复核 100 % 在 VERIFY 超时。**

    实测一轮 7 个证据包里中一个：L3_ANOMALY 触发、target_zoom = 1.493，
    状态机走完 AIM/ZOOM/CAPTURE 进 VERIFY 干等到超时，最后落一个
    density_ratio = 0 的 INCONCLUSIVE，全程没有一行报错。
    """
    n = Node(target_zoom=1.493)
    n._last_status = status(zoom=1.493)
    assert n.verify_due() is True, "巡航期就已经看得比较清楚的目标复核不了"


def test_a_small_steady_state_error_still_counts_as_arrived():
    """云台停稳后仍有残余抖动，卡死等号会让复核概率性超时。"""
    n = Node(target_zoom=2.40)
    n._last_status = status(zoom=2.31)          # 差 3.7 %
    assert n.verify_due() is True


def test_fires_when_no_zoom_was_needed_at_all():
    """目标已经够大时状态机算出的倍率就是 1.0，变焦这一维根本不动。

    此时只剩"云台指向偏离了巡航姿态"能区分在不在复核。
    """
    n = Node(target_zoom=1.0)
    n._last_status = status(zoom=1.0, pan=110.0)
    assert n.verify_due() is True


# ---------------------------------------------------------------- 不该发
def test_does_not_fire_during_aim_before_the_zoom_happens():
    """**这条钉的是修上一个 bug 时顺手引入的第二个 bug。**

    AIM 的第一步是一条前馈粗对准 `PTZ_SET(pan=目标方位, zoom=1.00)`。那一刻
    底盘停着、云台到位、对焦锁定、指向也早已偏离巡航姿态——如果判据只看
    "离开巡航姿态"，四条全中，verify_due() 就在 ZOOM 还没开始时命中了。

    而它一命中就立刻发报文并清掉 event_id：复核报的是放大之前的画面，
    密度比 ≈ 1.0。实测 1.07——证据包看起来完好，复核其实没发生。
    """
    n = Node(target_zoom=2.4)
    n._last_status = status(zoom=1.0, pan=110.0)     # 已对准，还没变焦
    assert n.verify_due() is False, "AIM 刚结束就发了复核报文，整次复核作废"


def test_does_not_fire_while_the_car_is_moving():
    n = Node(target_zoom=2.4)
    n._last_status = status(state="MOVING", zoom=2.4)
    assert n.verify_due() is False


def test_does_not_fire_before_the_gimbal_settles():
    n = Node(target_zoom=2.4)
    n._last_status = status(zoom=2.4, at_target=False)
    assert n.verify_due() is False


def test_does_not_fire_before_focus_locks():
    """变焦后景深变浅，没对上焦的图送进二级模型只会浪费一次复核预算。"""
    n = Node(target_zoom=2.4)
    n._last_status = status(zoom=2.4, focus="SEARCHING")
    assert n.verify_due() is False


def test_does_not_fire_when_the_car_is_parked_at_the_route_end():
    """车停在路尽头、云台还盯着柜列——四条里三条成立，但没有复核在进行。"""
    n = Node(target_zoom=1.0)
    n._last_status = status(zoom=1.0, pan=90.0, tilt=2.0)
    assert n.verify_due() is False


def test_cruise_jitter_is_not_mistaken_for_aiming():
    n = Node(target_zoom=1.0)
    n._last_status = status(zoom=1.0, pan=82.0, tilt=2.0)   # 偏 8° < 12°
    assert n.verify_due() is False


@pytest.mark.parametrize("pan", [90.0, -90.0])
def test_both_cruise_bearings_count_as_cruise(pan):
    """车沿过道往返，掉头之后巡航指向是 -90°，不能只认 +90°。"""
    n = Node(target_zoom=1.0)
    n._last_status = status(zoom=1.0, pan=pan, tilt=2.0)
    assert n.verify_due() is False


def test_no_status_yet_means_not_due():
    n = Node()
    assert n.verify_due() is False


# ---------------------------------------------------------------- 同源
def test_expected_zoom_matches_what_the_fsm_will_command():
    """**两边必须用同一个公式和同一个 p，不能各算各的。**

    perception 靠这个值判断 ZOOM 走完没有；和状态机差一点，就会早触发或
    晚触发一拍，而早触发一拍等于把整次复核废掉。
    """
    n = Node()
    entries = [{"track_id": 7, "pixel_density_px": 49.9},
               {"track_id": 9, "pixel_density_px": 300.0}]

    class Det:
        track_id = 7

    got = n._expected_zoom((0.9, Det(), "CONF_BAND"), entries, 1.0)
    want = zoom_for_density(1.0, 49.9, 120.0, 3.0)
    assert got == pytest.approx(want)


def test_expected_zoom_is_conservative_when_the_track_is_missing():
    """查不到那条 track 时取最大倍率——宁可晚触发，也不早触发。

    晚触发的代价是一次 VERIFY 超时（有记录、能查）；早触发的代价是一次
    看起来正常、其实什么都没复核的证据包（查不出来）。
    """
    n = Node()

    class Det:
        track_id = 42

    assert n._expected_zoom((0.9, Det(), "X"), [], 1.0) == pytest.approx(3.0)
    assert n._expected_zoom(None, [], 1.0) == pytest.approx(3.0)


def test_the_dead_constant_is_gone():
    """verify_zoom_min 删掉了。留着会让下一个人以为它还在起作用。"""
    cfg = Config.load()
    assert "verify_zoom_min" not in (cfg.get("mission.capture") or {})


# ---------------------------------------------------------------- 兜底
def test_the_fallback_fires_after_the_grace_period():
    """**判据本身可能再出错，所以要有一条让"出错"变得看得见的兜底。**

    两次事故（判据偏严 / 偏松）的共同点不是判据写错，而是错了之后**没有
    任何征兆**：状态机干等到超时，证据包留下一个 density_ratio = 0 的
    INCONCLUSIVE，日志里一行报错都没有。

    所以：四条无歧义的条件稳定保持够久、只有倍率对不上时，照发不误并打
    一条 WARN。复核质量差一点在证据包里看得出来，静默失效看不出来。
    """
    n = Node(target_zoom=2.4, grace=0.0)
    n._last_status = status(zoom=1.2, pan=110.0)
    assert n.verify_due() is False, "第一拍只记时刻，不该立刻兜底"
    assert n.verify_due() is True, "宽限期到了还不发，状态机就只能等超时"
    assert n.log.warns and "兜底" in n.log.warns[0][0]
    assert n.log.warns[0][1]["want"] == 2.4 and n.log.warns[0][1]["got"] == 1.2


def test_the_fallback_does_not_fire_early():
    n = Node(target_zoom=2.4, grace=99.0)
    n._last_status = status(zoom=1.2, pan=110.0)
    assert n.verify_due() is False
    assert n.verify_due() is False
    assert not n.log.warns


def test_the_grace_timer_resets_when_the_car_moves_again():
    """车又开起来了就不是"停在那儿等"，计时必须清零，否则下次会提前兜底。"""
    n = Node(target_zoom=2.4, grace=0.0)
    n._last_status = status(zoom=1.2, pan=110.0)
    n.verify_due()
    n._last_status = status(state="MOVING", zoom=1.0, pan=90.0)
    assert n.verify_due() is False
    n._last_status = status(zoom=1.2, pan=110.0)
    assert n.verify_due() is False, "计时没清零，第一拍就兜底了"


def test_a_normal_verify_does_not_leave_the_timer_running():
    n = Node(target_zoom=2.4, grace=0.0)
    n._last_status = status(zoom=1.2, pan=110.0)
    n.verify_due()
    n._last_status = status(zoom=2.4, pan=110.0)
    assert n.verify_due() is True and n._verify_ready_since_ns == 0
    assert not n.log.warns, "正常路径不该打兜底 WARN"
