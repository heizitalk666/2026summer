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


class LoopbackLink:
    """给测试用的内存链路：两端各持一个，A 写进去 B 读得到。

    有了它，串口编解码与假小车的往返可以在**不占用任何真实设备**的前提下
    跑单元测试；PTY 那条路留给 tools/fakecar 做真·端口联调。
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
