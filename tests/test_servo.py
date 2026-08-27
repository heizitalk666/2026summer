"""云台 PID 视觉伺服：三项动态指标 + 增益调度的必要性。

方案书 §2.3 / §9.3 的控制性能指标：
    超调量 ≤10 %、调节时间 ≤1.5 s、稳态误差 ≤20 px

闭环里的被控对象是 ptz_stub，有角速度上限、角加速度 240 °/s²、齿隙 0.05°、
到位抖动 0.15°——整定出来的参数与实测曲线因此是真的，不是仿真出来的
漂亮数字。
"""
from __future__ import annotations

import numpy as np
import pytest

from patrol.common.config import Config
from patrol.mission.servo import AxisPID, GimbalServo, PIDGains
from patrol.tools.tune_pid import closed_loop, pixel_of


@pytest.fixture(scope="module")
def gains(cfg):
    s = cfg.get("mission.servo")
    return PIDGains(kp=float(s["kp"]), ki=float(s["ki"]), kd=float(s["kd"]))


# ---------------------------------------------------------------- 单元
def test_gain_schedule_divides_by_zoom(cfg):
    """Δφ = e·θ/(W·z)：变焦 3× 时同样的像素偏差只对应三分之一的角度偏差。"""
    g = PIDGains(kp=1.0, ki=0.0, kd=0.0)
    pid = AxisPID(g, period_s=0.1, omega_max_dps=1e6, hfov_at_1x_deg=60.0,
                  image_span_px=1920, alpha=1.0)
    w1 = pid.step(100.0, zoom=1.0)
    pid.reset()
    w3 = pid.step(100.0, zoom=3.0)
    assert w3 == pytest.approx(w1 / 3.0, rel=1e-6)


def test_gain_schedule_off_keeps_wide_angle_factor(cfg):
    g = PIDGains(kp=1.0, ki=0.0, kd=0.0)
    pid = AxisPID(g, period_s=0.1, omega_max_dps=1e6, hfov_at_1x_deg=60.0,
                  image_span_px=1920, alpha=1.0, gain_schedule=False)
    assert pid.step(100.0, zoom=3.0) == pytest.approx(pid_step_ref(100.0), rel=1e-6)


def pid_step_ref(e: float) -> float:
    return 1.0 * e * 60.0 / 1920.0


def test_integral_separation_freezes_integral(cfg):
    """|e| 超过阈值时暂停积分累积，避免限幅期间积分膨胀。"""
    g = PIDGains(kp=0.0, ki=1.0, kd=0.0)
    pid = AxisPID(g, period_s=0.1, omega_max_dps=1e6, hfov_at_1x_deg=60.0,
                  image_span_px=1920, integral_separation_px=150.0, alpha=1.0)
    for _ in range(10):
        pid.step(400.0, zoom=1.0)       # 大偏差
    assert all(s.integral_frozen for s in pid.history)
    assert pid.history[-1].i_term == 0.0
    for _ in range(5):
        pid.step(50.0, zoom=1.0)        # 回到阈值内
    assert pid.history[-1].i_term > 0.0


def test_output_is_clamped(cfg):
    g = PIDGains(kp=100.0, ki=0.0, kd=0.0)
    pid = AxisPID(g, period_s=0.1, omega_max_dps=60.0, hfov_at_1x_deg=60.0,
                  image_span_px=1920, alpha=1.0)
    assert abs(pid.step(900.0, zoom=1.0)) == pytest.approx(60.0)
    assert pid.history[-1].saturated


def test_lowpass_filters_feedback_noise(cfg):
    """形心像素坐标有帧间抖动，直接送 PID 会让微分项高频振荡。"""
    g = PIDGains(kp=1.0, ki=0.0, kd=0.0)
    pid = AxisPID(g, period_s=0.1, omega_max_dps=1e6, hfov_at_1x_deg=60.0,
                  image_span_px=1920, alpha=0.6)
    rng = np.random.default_rng(0)
    raw, filt = [], []
    for _ in range(400):
        e = 100.0 + float(rng.normal(0, 30))
        pid.step(e, zoom=1.0)
        raw.append(e)
        filt.append(pid.history[-1].error_filtered_px)
    # 一阶惯性滤波对白噪声的方差衰减因子是 α/(2−α)，α=0.6 时为 0.4286，
    # 即标准差比 √0.4286 ≈ 0.655。判据取 0.75 留出采样估计的余量。
    ratio = float(np.std(filt[20:]) / np.std(raw[20:]))
    assert ratio == pytest.approx(0.655, abs=0.08), "实测衰减比 %.3f" % ratio


def test_servo_sign_convention(cfg):
    """目标在画面右侧时 pan 应减小，与 aim_offset 的增量语义一致。"""
    s = GimbalServo(cfg)
    pan_right, _ = s.step(1600.0, 540.0, zoom=1.0)
    s.reset()
    pan_left, _ = s.step(320.0, 540.0, zoom=1.0)
    assert pan_right < 0 < pan_left


# ---------------------------------------------------------------- 闭环
def test_pixel_of_is_pinhole(cfg):
    """闭环里用的投影必须与 scene/optics 同源，否则测的是另一个系统。"""
    W = 1920
    assert pixel_of(0.0, 0.0, zoom=1.0, hfov_1x=60.0, width=W) == pytest.approx(W / 2)
    # 目标在右前方（pan 需要减小）→ 落在画面右半边
    assert pixel_of(-10.0, 0.0, zoom=1.0, hfov_1x=60.0, width=W) > W / 2


@pytest.mark.parametrize("zoom", [1.0, 3.0])
def test_closed_loop_meets_spec(cfg, gains, zoom):
    """**三项动态指标**：超调 ≤10 %、调节时间 ≤1.5 s、稳态 ≤20 px。"""
    pid, _ = closed_loop(cfg, gains=gains, zoom=zoom, duration_s=3.0, seed=0)
    m = pid.metrics(deadband_px=20.0)
    assert m["overshoot_pct"] <= 10.0, m
    assert m["settling_time_s"] is not None and m["settling_time_s"] <= 1.5, m
    assert m["steady_error_px"] <= 20.0, m


def test_gain_schedule_is_necessary(cfg, gains):
    """**反证：关掉增益调度后变焦到 3× 会大幅超调。**

    方案书 §6.3.2 说这一条可以在演示中现场展示作为对比——这里把它固化成
    测试，免得哪天有人"顺手"把调度删了还以为没影响。
    """
    on, _ = closed_loop(cfg, gains=gains, zoom=3.0, gain_schedule=True,
                        duration_s=3.0, seed=0)
    off, _ = closed_loop(cfg, gains=gains, zoom=3.0, gain_schedule=False,
                         duration_s=3.0, seed=0)
    m_on = on.metrics(deadband_px=20.0)
    m_off = off.metrics(deadband_px=20.0)
    assert m_on["overshoot_pct"] <= 10.0
    assert m_off["overshoot_pct"] > 25.0, \
        "关掉调度后应当明显超调，实测 %.1f %%" % m_off["overshoot_pct"]


def test_design_doc_initial_gains_are_too_small(cfg):
    """方案书表 6-4 的 0.40/0.05/0.02 是**初值**不是整定结果。

    实测 Kp=0.40 时闭环时间常数约 2.75 s，3 s 内进不了 ±20 px 带。
    这条固化下来，是为了让"必须整定"这件事有据可依，而不是照抄初值就上。
    """
    pid, _ = closed_loop(cfg, gains=PIDGains(kp=0.40, ki=0.05, kd=0.02),
                         duration_s=3.0, seed=0)
    m = pid.metrics(deadband_px=20.0)
    assert m["settling_time_s"] is None or m["settling_time_s"] > 1.5
    assert m["steady_error_px"] > 20.0


def test_position_increment_fallback_also_works(cfg, gains):
    """差异清单 A1 的备选乙：不改白名单，用 PTZ_SET 位置增量多轮闭环。

    能收敛，但每周期要走一次 REQ/REP 往返，且 at_target 语义被破坏——
    这两条代价写在差异清单里，这里只验证它确实是可行的退路。
    """
    pid, _ = closed_loop(cfg, gains=gains, zoom=1.0, duration_s=3.0, seed=0,
                         use_rate=False)
    m = pid.metrics(deadband_px=20.0)
    assert m["steady_error_px"] <= 30.0


def test_both_axes_are_negative_feedback(cfg, gains):
    """两个通道的符号必须都是负反馈。**曾经俯仰通道多了一个负号。**

    几何：相机 y 轴朝下，目标在画面上方时 cy < H/2，故 e = H/2 − cy > 0；
    tilt 正方向是抬头（PinholeCamera 里 fwd.z = sin(tilt)），抬头把上方的
    目标带回画面中心，所以此时 tilt_dps 必须为正。方位同理，目标在右侧
    （cx > W/2）时 e < 0，pan 必须减小（相机方位角 = yaw + pan，map 系逆时针
    为正，pan 减小即向右转）。

    这条没被覆盖的后果实测过：俯仰正反馈把 2° 的初始偏差一路放大，约 1 s
    后目标垂直出框，AIM 只能干等到超时 ABORT——而链路上唯一的症状是"检出
    突然归零"，极易误判成检测器漏检或渲染出错。
    """
    servo = GimbalServo(cfg)
    W, H = servo.W, servo.H

    pan_r, _ = servo.step(W * 0.9, H / 2.0, zoom=1.0)      # 目标偏右
    servo.reset()
    pan_l, _ = servo.step(W * 0.1, H / 2.0, zoom=1.0)      # 目标偏左
    assert pan_r < 0 < pan_l, "方位通道符号反了 (右=%.2f 左=%.2f)" % (pan_r, pan_l)

    servo.reset()
    _, tilt_up = servo.step(W / 2.0, H * 0.1, zoom=1.0)    # 目标偏上
    servo.reset()
    _, tilt_dn = servo.step(W / 2.0, H * 0.9, zoom=1.0)    # 目标偏下
    assert tilt_dn < 0 < tilt_up, \
        "俯仰通道符号反了 (上=%.2f 下=%.2f)" % (tilt_up, tilt_dn)


def test_tilt_axis_converges_in_closed_loop(cfg, gains):
    """俯仰通道对着真实被控对象闭环，必须收敛而不是发散。

    只测方位通道是上一版留下的窟窿：单轴测试全绿，跑起来俯仰照样发散。
    """
    servo = GimbalServo(cfg)
    W, H = servo.W, servo.H
    theta_v = 60.0 * H / W
    tilt_deg = 2.0                       # 巡航俯仰角
    target_el_deg = 0.0                  # 目标与相机等高
    dt = servo.period_s
    errs = []
    for k in range(40):
        # 目标相对光轴的角度 → 像素坐标（小角度下线性）
        cy = H / 2.0 - (target_el_deg - tilt_deg) * H / theta_v
        _, tilt_dps = servo.step(W / 2.0, cy, zoom=1.0, t_s=k * dt)
        tilt_deg += tilt_dps * dt        # 云台按速率积分
        errs.append(abs(target_el_deg - tilt_deg))
    assert errs[-1] < 0.2, "俯仰未收敛，残差 %.2f°（轨迹尾段 %s）" % (
        errs[-1], [round(e, 2) for e in errs[-5:]])
    assert errs[-1] < errs[0], "俯仰在发散"
