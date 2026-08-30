"""TCP 环回链路：Windows 上"真驱动 + 假硬件"那一层的可用性。

**为什么需要这一层，以及为什么它不能只有 PTY 一条路。**

没有硬件的时候，能证明"上位机侧代码是真的"的只有一条路：让真驱动
（`ChassisSerial`）去跟一个说同样协议的假硬件对话。桩模式证明不了这件事——
桩里驱动层整个被换掉了，串口编解码、超时、重传一行都没跑。

原来这条路只有伪终端一种实现，而 `os.openpty()` 是 POSIX 专有的：Windows 上
`hasattr(os, "openpty")` 是 False，`_PtyLink` 连构造都构造不出来。于是在
Windows 机器上，这一层演示能力等于零——而分工文档里"假小车串口往返"是明确
列出的验收项。TCP 环回把它补回来。

**这些测试钉住的是什么。**不是"TCP 能通"（那是操作系统的事），而是：

1. `open_link` 的分发规则——改 configs 里的 port 就能在真串口与假小车之间
   切，驱动代码一行不动。这条规则错了，`driver_mode: real` 会去开一个不存在
   的 COM 口，报错还很难懂。
2. 两个端点的字节语义与 `SerialLink` 一致：读超时返回空字节而不是抛异常。
   这条错了，上层的 keepalive 判活会把"这一轮没数据"当成"链路断了"。
3. 断开后能重新接受连接。上位机重启时假小车不该跟着重启——演示中途重启
   东西很难看，而且会打断叙事。
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

from patrol.drivers.real.serial_link import SerialLink, TcpLink, open_link
from patrol.tools.fakecar import _TcpLink


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------------------------------------------------------------- 分发规则
@pytest.mark.parametrize("port", ["tcp://127.0.0.1:5760", "tcp://localhost:9999"])
def test_open_link_routes_tcp_ports_to_tcplink(port, monkeypatch):
    """tcp:// 前缀走 TcpLink。这里只验分发，不真连——连接由下面的往返测试覆盖。"""
    seen = {}

    class _Fake:
        def __init__(self, host, p, read_timeout_s=0.05):
            seen["host"], seen["port"] = host, p

    monkeypatch.setattr("patrol.drivers.real.serial_link.TcpLink", _Fake)
    open_link(port)
    assert seen["host"] in ("127.0.0.1", "localhost")
    assert seen["port"] in (5760, 9999)


@pytest.mark.parametrize("bad", ["tcp://nope", "tcp://1.2.3.4:abc", "tcp://:80"])
def test_open_link_rejects_malformed_tcp_ports(bad):
    """写错了要当场报错，不能退回去开串口——那样错误信息会指向完全无关的地方。"""
    with pytest.raises(ValueError, match="tcp://host:port"):
        open_link(bad)


def test_open_link_routes_device_paths_to_seriallink(monkeypatch):
    """非 tcp:// 一律走真串口。COM3 与 /dev/ttyUSB0 都要落到 SerialLink。"""
    seen = {}

    class _Fake:
        def __init__(self, port, baudrate=115200, read_timeout_s=0.05):
            seen["port"] = port

    monkeypatch.setattr("patrol.drivers.real.serial_link.SerialLink", _Fake)
    open_link("COM3")
    assert seen["port"] == "COM3"
    open_link("/dev/ttyUSB0")
    assert seen["port"] == "/dev/ttyUSB0"


# ---------------------------------------------------------------- 字节往返
def test_tcp_link_round_trip():
    """两端字节双向可达，且读超时返回空字节而不是抛异常。

    空字节这条是与 SerialLink 对齐的关键：上层靠 status_timeout_s / keepalive_s
    判链路死活，如果"这一轮没数据"变成抛异常，判活逻辑会误判成断链。
    """
    port = _free_port()
    server = _TcpLink(port=port)
    try:
        # 服务端要被 read() 驱动才会 accept，所以起一个泵
        stop = threading.Event()
        got = bytearray()

        def pump():
            while not stop.wait(0.005):
                got.extend(server.read())

        t = threading.Thread(target=pump, daemon=True)
        t.start()

        client = TcpLink("127.0.0.1", port, read_timeout_s=0.05)
        try:
            client.write(b"$PING,1*00\n")
            deadline = time.time() + 5
            while time.time() < deadline and b"PING" not in bytes(got):
                time.sleep(0.02)
            assert b"$PING,1*00\n" in bytes(got), "上位机→假小车方向不通"

            server.write(b"$STA,1,MOVING*7E\n")
            back = b""
            deadline = time.time() + 5
            while time.time() < deadline and b"STA" not in back:
                back += client.read()
                time.sleep(0.02)
            assert b"$STA,1,MOVING*7E\n" in back, "假小车→上位机方向不通"

            # 没有数据时必须是空字节，不是异常
            assert client.read() == b""
        finally:
            stop.set()
            client.close()
    finally:
        server.close()


def test_tcp_link_accepts_a_second_connection_after_disconnect():
    """上位机重启后假小车还能接上——不必跟着重启。

    演示时 run_all 会起停多次，假小车是单独一个终端窗口。如果它在第一次断开
    后就变成聋子，每演示一轮都要回去重启它。
    """
    port = _free_port()
    server = _TcpLink(port=port)
    try:
        stop = threading.Event()
        got = bytearray()

        def pump():
            while not stop.wait(0.005):
                got.extend(server.read())

        threading.Thread(target=pump, daemon=True).start()

        for i in range(2):
            c = TcpLink("127.0.0.1", port, read_timeout_s=0.05)
            probe = b"$PING,%d*00\n" % (i + 1)
            c.write(probe)
            deadline = time.time() + 5
            while time.time() < deadline and probe not in bytes(got):
                time.sleep(0.02)
            assert probe in bytes(got), "第 %d 次连接没通" % (i + 1)
            c.close()
            time.sleep(0.15)          # 给服务端一轮 read 去发现对端已关
        stop.set()
    finally:
        server.close()


def test_tcp_link_write_before_anyone_connects_is_dropped_silently():
    """没人连的时候写状态帧应当静默丢弃，和对着空串口写一样，不能抛。

    假小车是 20 Hz 无条件发状态的；如果开着没人连就炸，它根本活不到上位机
    连上来的那一刻。
    """
    port = _free_port()
    server = _TcpLink(port=port)
    try:
        server.write(b"$STA,1,IDLE*00\n")     # 不抛即通过
        assert server.read() == b""
    finally:
        server.close()
