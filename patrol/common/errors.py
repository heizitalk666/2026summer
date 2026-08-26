"""驱动层异常。ICD §8.2。

网关捕获这些异常后转成 COMMAND_ACK 的 reject_code，见 gateway/node.py。
"""
from __future__ import annotations


class DriverError(Exception):
    """驱动层异常基类。"""


class DriverNotReady(DriverError):
    """硬件未初始化或已断开。"""


class ParamOutOfRange(DriverError):
    """参数超出硬件能力。不截断，直接抛。

    注意与网关的范围校验区分：网关的依据是任务需求（ICD §4.3 的硬编码常量），
    驱动的依据是硬件手册（capabilities()）。两层校验依据不同，都要有。
    """


class DriverTimeout(DriverError):
    """底层通信超时。与状态机的状态超时是两回事。"""


class CapabilityError(DriverError):
    """开机自检失败：驱动声明的能力不满足任务需求。

    ICD §8.1 第三条约定：PTZCaps.max_zoom < 3.0 时系统直接拒绝启动，
    而不是等到现场发现表读不出来。
    """
