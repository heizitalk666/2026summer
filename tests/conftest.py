import random
import pytest

from patrol.common.config import Config


@pytest.fixture(scope="session")
def cfg():
    return Config.load(overrides=_CHEAP_L3)


@pytest.fixture()
def free_ports():
    """给每个测试一组不冲突的 tcp 端口，避免并行时抢占。"""
    base = random.randint(21000, 44000)
    return {"detection": "tcp://127.0.0.1:%d" % base,
            "command": "tcp://127.0.0.1:%d" % (base + 1),
            "status": "tcp://127.0.0.1:%d" % (base + 2)}


#: 单元测试默认用**零权重的统计法**做 L3，不加载 PaDiM。
#:
#: 三个理由，按重要性排：
#:
#: 1. **不能让单元测试依赖一个 83 MB 的权重文件。**`configs/system.yaml` 现在
#:    是 `l3.model: padim_s` + `weights: training/runs/anomaly/padim_cov.pt`，
#:    而那个文件不进版本库。别人 clone 下来跑 pytest，`build_anomaly` 会静默
#:    退回统计法、测试照样全过——**等于这些用例从来没验过它们以为在验的后端**。
#: 2. **它会把断言时长的用例挤挂。**PaDiM 每次打分 17 ms、构造 2.6 s，
#:    四个测试文件反复构造。实测全套跑时 `test_events_pass_schema_and_meet_latency`
#:    P95 从 49 ms 涨到 108 ms 而单独跑只有 49 ms——失败的是测量不是被测对象，
#:    正是这个文件下面 `_retry` 那段注释说的情形。
#: 3. 统计法构造 0.003 s、打分 0.6 ms，接口与 PaDiM 完全一致，
#:    这些用例验的是**接口契约与降级行为**，不是异常检测的准确率。
#:
#: 要专门验 PaDiM 那一路的，自己传 overrides 覆盖回去（并对权重缺席 skip）。
_CHEAP_L3 = {"perception": {"l3": {"model": "statistical", "weights": None}}}


def _merge(*maps):
    """浅合并 overrides，逐 key 深合并一层——够用且不引入依赖。"""
    out: dict = {}
    for m in maps:
        for k, v in (m or {}).items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                merged = dict(out[k])
                for k2, v2 in v.items():
                    if isinstance(v2, dict) and isinstance(merged.get(k2), dict):
                        merged[k2] = {**merged[k2], **v2}
                    else:
                        merged[k2] = v2
                out[k] = merged
            else:
                out[k] = v
    return out


@pytest.fixture()
def cfg_ports(free_ports):
    return Config.load(overrides=_merge(_CHEAP_L3, {"bus": free_ports}))


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
