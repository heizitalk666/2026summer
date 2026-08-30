"""串口链路薄封装。

pyserial 只在真正打开端口时才 import——桩环境不装 pyserial 也能跑
（requirements.txt 里它是有的，但 CI 或同学的机器上未必；缺一个只有真机
才用得到的库不该让整套系统起不来）。

链路层只管字节进出，不认识协议。协议在 serial_protocol.py，两边共用。
"""
from __future__ import annotations

import threading


class SerialLink:
    """一条串口。读非阻塞（超时返回空），写整帧加锁。"""

    def __init__(self, port: str, baudrate: int = 115200,
                 read_timeout_s: float = 0.05):
        try:
            import serial                    # noqa: PLC0415
        except ImportError as e:             # pragma: no cover - 真机才走到
            raise RuntimeError(
                "真机模式需要 pyserial：pip install pyserial") from e
        self.port = port
        self._ser = serial.Serial(port=port, baudrate=int(baudrate),
                                  bytesize=8, parity="N", stopbits=1,
                                  timeout=float(read_timeout_s), write_timeout=1.0)
        self._wlock = threading.Lock()

    def read(self) -> bytes:
        n = max(1, self._ser.in_waiting)
        return self._ser.read(n)

    def write(self, data: bytes) -> None:
        with self._wlock:
            self._ser.write(data)

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:                    # noqa: BLE001
            pass


class TcpLink:
    """一条 TCP 环回链路，字节语义与 SerialLink 完全一致。

    **它存在的理由是 Windows 没有伪终端。**假小车（tools/fakecar）在 POSIX
    上用 ``os.openpty()`` 造一个 ``/dev/pts/N``，上位机当普通串口打开，于是
    "真驱动 + 假硬件"这条联调路跑得通。Windows 上 ``os.openpty`` 根本不存在
    （``hasattr(os, "openpty")`` 为 False），整条路就断了——而没有硬件的时候，
    这恰恰是唯一能证明"上位机侧代码是真的、不是桩里的理想链路"的一层。

    TCP 环回是跨平台的等价物，而且**保住了真正要紧的那个性质：进程边界是真的**。
    假小车是独立进程，字节真的穿过内核，收发、分帧、超时、重传、2 % ACK 丢包
    注入全部照原样发生。换掉的只是承载——UART 换成 loopback socket。

    不等价的地方要说清楚，别在答辩上被问漏：没有波特率、没有线路噪声、没有
    帧错误，TCP 还保证有序不丢。所以它能证明**协议栈与时序逻辑**是对的，
    不能证明物理层。物理层要等真串口。
    """

    def __init__(self, host: str, port: int, read_timeout_s: float = 0.05,
                 connect_timeout_s: float = 5.0):
        import socket                        # noqa: PLC0415
        self.port = "tcp://%s:%d" % (host, int(port))
        self._sock = socket.create_connection((host, int(port)),
                                              timeout=float(connect_timeout_s))
        # 读超时即"这一轮没数据"，与 SerialLink.read 的语义对齐：返回空字节，
        # 不抛异常。上层靠 status_timeout_s / keepalive_s 判链路死活。
        self._sock.settimeout(float(read_timeout_s))
        self._wlock = threading.Lock()

    def read(self) -> bytes:
        try:
            return self._sock.recv(4096)
        except OSError:
            # TimeoutError 是 OSError 的子类，超时与断开在这里都退化成"没数据"。
            return b""

    def write(self, data: bytes) -> None:
        with self._wlock:
            try:
                self._sock.sendall(data)
            except OSError:
                pass

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def open_link(port: str, baudrate: int = 115200, read_timeout_s: float = 0.05):
    """按端口串选链路实现。这是桩/真机之外唯一的一处分发。

        tcp://127.0.0.1:5760   → TcpLink（假小车，跨平台）
        /dev/ttyUSB0, COM3     → SerialLink（真串口）

    放在这里而不是各驱动里，是为了让底盘与云台共用同一条规则：改 configs
    里的 port 就能在"真串口"和"假小车"之间切，**驱动代码一行不动**。
    这与 driver_mode 的设计是同一个思路——切换点只有一个，且在配置里。
    """
    p = str(port).strip()
    if p.startswith("tcp://"):
        hostport = p[len("tcp://"):]
        host, _, sport = hostport.rpartition(":")
        if not host or not sport.isdigit():
            raise ValueError("tcp 端口要写成 tcp://host:port，收到 %r" % port)
        return TcpLink(host, int(sport), read_timeout_s=read_timeout_s)
    return SerialLink(port=p, baudrate=baudrate, read_timeout_s=read_timeout_s)


class LoopbackLink:
    """给测试用的内存链路：两端各持一个，A 写进去 B 读得到。

    有了它，串口编解码与假小车的往返可以在**不占用任何真实设备**的前提下
    跑单元测试；PTY 与 TCP 那两条路留给 tools/fakecar 做真·端口联调。
    """

    def __init__(self) -> None:
        self._to_a = bytearray()
        self._to_b = bytearray()
        self._lock = threading.Lock()
        self.closed = False

    def _endpoint(self, out: bytearray, inp: bytearray) -> "LoopbackLink._End":
        return LoopbackLink._End(self, out, inp)

    def side_a(self):
        return self._endpoint(self._to_b, self._to_a)

    def side_b(self):
        return self._endpoint(self._to_a, self._to_b)

    class _End:
        def __init__(self, owner: "LoopbackLink", out: bytearray, inp: bytearray):
            self._o, self._out, self._in = owner, out, inp

        def read(self) -> bytes:
            with self._o._lock:             # noqa: SLF001
                data = bytes(self._in)
                del self._in[:]
            return data

        def write(self, data: bytes) -> None:
            with self._o._lock:             # noqa: SLF001
                self._out.extend(data)

        def close(self) -> None:
            self._o.closed = True
