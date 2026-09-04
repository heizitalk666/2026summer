"""ZeroMQ 消息总线。ICD §1.2。

四条接口的传输层：

| 接口 | 模式        | 用途                                       |
|------|-------------|--------------------------------------------|
| IF-1 | PUB/SUB     | perception → mission，10 Hz 巡航事件        |
| IF-2 | REQ/REP     | mission → gateway，强制每条指令必须有回执    |
| IF-3 | PUB/SUB     | gateway → mission + perception，一发多收     |

**为什么默认 tcp:// 而不是 ICD 原文的 ipc://**：ipc:// 在 Windows 上不可用，
而组里的笔记本多半是 Windows。ICD §1.2 已经预留了这个口子（"桩环境把
ipc:// 换成 tcp:// 就能跨机调试，代码不用改"），传输地址进 configs/system.yaml，
真机档案可切回 ipc。

**REQ/REP 的超时处理**：ZeroMQ 的 REQ 套接字是严格轮转的，一次请求超时后
套接字状态就废了，必须重建。chassis_stub 有 2 % 的 ACK 丢失率，一轮巡检
近 90 条指令大概率会丢一条，所以这里实现了 lazy-pirate 模式：超时即销毁
重建，不复用坏掉的套接字。
"""
from __future__ import annotations

import json
from typing import Any, Callable

import zmq

from patrol import SCHEMA_VERSION
from patrol.common.messages import VersionMismatch, check_version

DEFAULT_LINGER_MS = 0


class BusError(RuntimeError):
    pass


class RequestTimeout(BusError):
    """REQ 侧等 ACK 超时。网关没回 ACK 会阻塞 mission，这是有意为之：
    宁可状态机卡在超时上被日志记下来，也不要指令悄悄丢失（ICD §4.4）。"""


def _encode(msg: dict) -> bytes:
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _decode(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8"))


class Publisher:
    """PUB 端。绑定地址，一发多收。"""

    def __init__(self, addr: str, ctx: zmq.Context | None = None):
        self.addr = addr
        self._ctx = ctx or zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.PUB)
        self._sock.setsockopt(zmq.LINGER, DEFAULT_LINGER_MS)
        self._sock.bind(addr)

    def send(self, msg: dict) -> None:
        # 用 msg_type 当 topic，订阅方可以按类型过滤
        topic = str(msg.get("msg_type", "")).encode("ascii")
        self._sock.send_multipart([topic, _encode(msg)])

    def close(self) -> None:
        self._sock.close(0)


class Subscriber:
    """SUB 端。连接地址，可按 msg_type 过滤。"""

    def __init__(self, addr: str, topics: list[str] | None = None,
                 ctx: zmq.Context | None = None):
        self.addr = addr
        self._ctx = ctx or zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.SUB)
        self._sock.setsockopt(zmq.LINGER, DEFAULT_LINGER_MS)
        self._sock.connect(addr)
        for t in (topics or [""]):
            self._sock.setsockopt(zmq.SUBSCRIBE, t.encode("ascii"))

    def recv(self, timeout_ms: int = 100, check_ver: bool = True) -> dict | None:
        """收一条。超时返回 None。版本不匹配抛 VersionMismatch。"""
        if not self._sock.poll(timeout_ms, zmq.POLLIN):
            return None
        _topic, raw = self._sock.recv_multipart()
        msg = _decode(raw)
        if check_ver:
            check_version(msg)
        return msg

    def drain(self, max_n: int = 64, check_ver: bool = True) -> list[dict]:
        """把已到达的报文一次收干净，返回列表。

        StatusReport 是 20 Hz 周期报，状态机每个 tick 只关心最新状态，
        用它避免队列越积越多导致读到的是过期状态。
        """
        out: list[dict] = []
        for _ in range(max_n):
            m = self.recv(timeout_ms=0, check_ver=check_ver)
            if m is None:
                break
            out.append(m)
        return out

    def close(self) -> None:
        self._sock.close(0)


class Requester:
    """REQ 端（mission 侧）。超时即重建套接字，见模块文档。"""

    def __init__(self, addr: str, timeout_ms: int = 2000,
                 ctx: zmq.Context | None = None):
        self.addr = addr
        self.timeout_ms = timeout_ms
        self._ctx = ctx or zmq.Context.instance()
        self._sock: zmq.Socket | None = None
        self._open()

    def _open(self) -> None:
        self._sock = self._ctx.socket(zmq.REQ)
        self._sock.setsockopt(zmq.LINGER, DEFAULT_LINGER_MS)
        self._sock.connect(self.addr)

    def _reopen(self) -> None:
        if self._sock is not None:
            self._sock.close(0)
        self._open()

    def request(self, msg: dict, timeout_ms: int | None = None,
                check_ver: bool = True) -> dict:
        tmo = self.timeout_ms if timeout_ms is None else timeout_ms
        assert self._sock is not None
        self._sock.send(_encode(msg))
        if not self._sock.poll(tmo, zmq.POLLIN):
            self._reopen()   # REQ 状态已废，必须重建
            raise RequestTimeout(
                "等 ACK 超时 %d ms: %s" % (tmo, msg.get("command", msg.get("msg_type")))
            )
        reply = _decode(self._sock.recv())
        if check_ver:
            check_version(reply)
        return reply

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close(0)
            self._sock = None


class Replier:
    """REP 端（gateway 侧）。绑定地址，收一条回一条。"""

    def __init__(self, addr: str, ctx: zmq.Context | None = None):
        self.addr = addr
        self._ctx = ctx or zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.REP)
        self._sock.setsockopt(zmq.LINGER, DEFAULT_LINGER_MS)
        self._sock.bind(addr)

    def serve_once(self, handler: Callable[[dict], dict],
                   timeout_ms: int = 100) -> bool:
        """收一条请求、调 handler、回复。没有请求返回 False。

        handler 抛异常时仍然要回一条，否则 REQ 端会一直卡着——ZeroMQ 的
        REQ/REP 是严格轮转的，漏回一次整条链路就死锁了。
        """
        if not self._sock.poll(timeout_ms, zmq.POLLIN):
            return False
        raw = self._sock.recv()
        try:
            req = _decode(raw)
        except Exception as e:
            self._sock.send(_encode({"msg_type": "COMMAND_ACK",
                                     "_transport_error": str(e)}))
            return True
        try:
            rep = handler(req)
        except Exception as e:                       # noqa: BLE001
            rep = {"msg_type": "COMMAND_ACK", "_handler_error": repr(e),
                   "schema_version": req.get("schema_version", SCHEMA_VERSION),
                   "cmd_id": req.get("cmd_id")}
        self._sock.send(_encode(rep))
        return True

    def close(self) -> None:
        self._sock.close(0)


def endpoints(cfg) -> dict[str, str]:
    """从配置取四条接口的地址。"""
    return {
        "detection": cfg.get("bus.detection"),
        "command":   cfg.get("bus.command"),
        "status":    cfg.get("bus.status"),
    }
