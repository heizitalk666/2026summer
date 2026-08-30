import random
import pytest

from patrol.common.config import Config


@pytest.fixture(scope="session")
def cfg():
    return Config.load()


@pytest.fixture()
def free_ports():
    """给每个测试一组不冲突的 tcp 端口，避免并行时抢占。"""
    base = random.randint(21000, 44000)
    return {"detection": "tcp://127.0.0.1:%d" % base,
            "command": "tcp://127.0.0.1:%d" % (base + 1),
            "status": "tcp://127.0.0.1:%d" % (base + 2)}


@pytest.fixture()
def cfg_ports(free_ports):
    return Config.load(overrides={"bus": free_ports})


# ---------------------------------------------------------------- 抗负载辅助
#
# **为什么需要这两个。**桩（PTZStub / ChassisStub 等）用后台线程按**墙上时钟**
# 推进物理仿真：`self._stop.wait(dt)` → `_tick(dt)`。机器一忙线程被推迟，仿真就
# 走得比真实时间慢，于是任何「断言时长」的用例测到的都是失真值——**失败的是测量，
# 不是被测对象**。这一族用例以前每轮挂的都不一样，"一条都不许退化"因此形同虚设。
#
# 两个工具，用途严格区分，别混：
#
#   _wait_until(cond)   等一个**事实**成立（状态传播到了、句柄就绪了）。**首选。**
#                       它取代 time.sleep(固定值)：后者是在赌一个时长，前者等的是
#                       事实本身。空闲时反而更快（条件一成立立刻返回）。
#
#   _retry(measure, ok) 只在断言的是**时长/速率**、且噪声单向时才用——负载只会让
#                       时长变长、超调变小，不会反过来。所以"任意一次达标"足以
#                       证明被测对象达标，与 timeit 取 min 而不是 mean 同理。
#                       **不得用来掩盖真实退化**：被测对象真坏了，N 次会全挂。
#
# 能等事实就不要重试测量。


def _wait_until(cond, timeout_s: float = 5.0, poll_s: float = 0.005) -> bool:
    """轮询到 cond() 为真，或超时。返回是否等到。

    cond 抛异常按"还没就绪"处理——被等的状态往往在早期是 None，调用方不必
    每次都写一遍防御性判断。
    """
    import time
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        try:
            if cond():
                return True
        except Exception:                       # noqa: BLE001
            pass
        time.sleep(poll_s)
    return False


def _retry(measure, ok, attempts: int = 4, valid=None):
    """重复测到有效且达标为止。返回 (最后一次的度量, 是否达标)。

    `valid(m)` 可选：判断这一次的**测量本身**可不可信（比如这一轮明显被抢了
    CPU）。返回 False 的样本**不计入 attempts**，直接重测——于是重试次数只
    花在有效样本上，不会被几轮坏采样耗光。不传就认为每次都有效。

    为防被抢 CPU 的机器上无限重测，无效样本另设 3 倍上限。
    """
    m = None
    used = wasted = 0
    while used < attempts and wasted < attempts * 3:
        m = measure()
        if valid is not None and not valid(m):
            wasted += 1
            continue                            # 坏采样，不算一次尝试
        used += 1
        if ok(m):
            return m, True
    return m, False
