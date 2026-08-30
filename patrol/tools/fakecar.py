#!/usr/bin/env python3
"""假小车：说 docs/底盘串口协议.md 那一套协议的仿真底盘。

    python -m patrol.tools.fakecar --pty     # POSIX：建伪终端 /dev/pts/N
    python -m patrol.tools.fakecar --tcp     # Windows：TCP 环回 tcp://127.0.0.1:5760
    # 把打印出来的那一行填进 configs/real.yaml 的 real.serial.chassis.port，
    # 再把 configs/system.yaml 的 driver_mode 改成 real

**Windows 上必须用 --tcp。**``os.openpty()`` 是 POSIX 专有的，Windows 上不存在，
--pty 会直接失败；不给参数时会按平台自动选，所以照着敲哪一条都不会踩坑。
TCP 环回保住了"假小车是独立进程、字节真的过内核"这个关键性质，换掉的只是
承载（UART → loopback socket）。它不能替代的是物理层：没有波特率、没有线路
噪声、没有帧错误。

**它存在的理由是把"上位机串口这一侧"从等硬件里解放出来。**硬件没到、底盘
固件情况不明，但协议是我们定的，于是可以先造一个说同样协议的东西，把
chassis_serial 的收发、超时、重传、安全事件回调全部调通。硬件到货时上位机
这一侧已经是跑过的代码，只改一个端口名。

**车的行为直接复用 `ChassisStub`**，不另写一套。这一点很要紧：桩里注入的
停车延迟 1.5–2.5 s、2 % ACK 丢包、随机障碍物与急停，全都会原样出现在串口
链路上。要是这里另写一个"理想底盘"，串口层就只在顺境里被测过，而串口链路
恰恰是最容易在逆境里出问题的地方。

也可以不建任何端口，直接在进程内用 `serial_link.LoopbackLink` 对接
（`FakeCar(cfg, loop.side_b())`），单元测试走这条——见 tests/test_serial_protocol.py。
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

from patrol.common.config import Config
from patrol.drivers.base import ExecProgress
from patrol.drivers.real import serial_protocol as P
from patrol.drivers.stub.chassis_stub import ChassisStub
from patrol.scene.world import World


class FakeCar:
    """底盘固件的仿真：收指令、回 ACK、20 Hz 发状态、安全事件立刻发。"""

    def __init__(self, cfg, link, *, seed: int = 0, verbose: bool = False):
        self.cfg = cfg
        self.link = link
        self.verbose = verbose
        self.world = World(cfg)
        self.chassis = ChassisStub(cfg, self.world, seed=seed)
        self.max_creep_m = float(cfg.get("stub.chassis.max_creep_m", 0.5))
        self._seq = 0
        self._reader = P.LineReader()
        self._pending: list[tuple[str, object]] = []   # (kind, handle)
        self._last_status = 0.0
        self._last_ping = time.monotonic()
        self.keepalive_s = float(cfg.get("real.serial.chassis.keepalive_s", 3.0))
        self._keepalive_tripped = False
        self._stop = threading.Event()
        self.chassis.subscribe_safety(self._on_safety)

    # ------------------------------------------------------------
    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq

    def _emit(self, data: bytes) -> None:
        try:
            self.link.write(data)
        except OSError:
            pass
        if self.verbose:
            sys.stderr.write("<< " + data.decode("ascii", "replace"))

    def _on_safety(self, ev: dict) -> None:
        """安全事件不等周期，收到就发（协议 §4：≤20 ms）。"""
        self._emit(P.encode_safety(
            self._next_seq(), event=ev["event_type"], severity=ev["severity"],
            brake_ms=int(ev.get("brake_latency_ms", 0)),
            detail=str(ev.get("detail", ""))))

    def _ack(self, seq: int, result: str, detail: str = "") -> None:
        """回执。**被回执的 seq 在 payload 里，帧自己的 seq 是底盘的计数器。**

        协议 §3.2 写的是 `ACK,<seq>,<result>[,<detail>]`——两个 seq 是不同的
        东西。把被回执的 seq 塞进帧头会让上位机永远对不上句柄，表现为每条
        指令都超时，而链路上明明看得到 ACK 在飞。
        """
        self._emit(P.encode(P.RSP_ACK, self._next_seq(), seq, result,
                            detail.replace(",", ";")[:60]))

    # ------------------------------------------------------------ 指令
    def handle(self, f: P.Frame) -> None:
        if self.verbose:
            sys.stderr.write(">> %s %s %s\n" % (f.type, f.seq, ",".join(f.fields)))
        t = f.type
        if t == P.CMD_PING:
            self._last_ping = time.monotonic()
            self._keepalive_tripped = False
            return                                  # 保活不回执，省带宽
        if t == P.CMD_QUERY:
            self._send_status()
            return self._ack(f.seq, P.ACK_OK)
        try:
            if t == P.CMD_PAUSE:
                self._pending.append(("PAUSE", self.chassis.pause(f.field(0, "-"))))
            elif t == P.CMD_RESUME:
                self._pending.append(("RESUME", self.chassis.resume()))
            elif t == P.CMD_CREEP:
                mm = f.int_field(0, -1)
                if not (0 < mm <= int(self.max_creep_m * 1000)):
                    # **底盘固件自己再限一道。**上位机已经限过一次，这是纵深
                    # 防御不是冗余：上位机被改坏了，车也不会窜出去。
                    return self._ack(f.seq, P.ACK_REJECT, "CREEP_OUT_OF_RANGE")
                self._pending.append(
                    ("CREEP_FORWARD", self.chassis.creep_forward(mm / 1000.0)))
            elif t == P.CMD_GOTO:
                wp = f.field(0)
                if wp not in self.world.waypoints:
                    return self._ack(f.seq, P.ACK_REJECT, "UNKNOWN_WAYPOINT")
                self._pending.append(("GOTO_OBSERVE", self.chassis.goto_observe(
                    wp, max(0.1, f.int_field(1, 300) / 1000.0))))
            else:
                return self._ack(f.seq, P.ACK_REJECT, "UNKNOWN_TYPE")
        except Exception as e:                      # noqa: BLE001
            return self._ack(f.seq, P.ACK_REJECT, type(e).__name__)
        self._ack(f.seq, P.ACK_OK)

    # ------------------------------------------------------------ 上报
    def _send_status(self) -> None:
        st = self.chassis.status()
        self._emit(P.encode_status(
            self._next_seq(), state=st.state.value, speed_mps=st.speed_mps,
            path_progress=st.path_progress,
            distance_to_goal_m=st.distance_to_goal_m,
            waypoint_id=st.current_waypoint_id, battery_pct=st.battery_pct,
            safety_active=st.safety_layer_active))

    def _check_keepalive(self) -> None:
        """保活超时自行减速停车。

        这是**底盘侧的独立保护**，与 RK3576 上的网关看门狗是两回事：网关看门狗
        管"AI 进程死了"，保活管"串口断了或整个 RK3576 死了"。后者网关自己也
        救不了，只能由底盘兜底。两级都要有。
        """
        if self._keepalive_tripped:
            return
        if time.monotonic() - self._last_ping > self.keepalive_s:
            self._keepalive_tripped = True
            self.chassis.pause("KEEPALIVE_LOST")
            self._on_safety({"event_type": "MOTOR_FAULT", "severity": "WARN",
                             "brake_latency_ms": 0,
                             "detail": "上位机保活超时，底盘自行停车"})

    def step(self) -> None:
        try:
            chunk = self.link.read()
        except OSError:
            chunk = b""
        for line in self._reader.feed(chunk):
            try:
                f = P.decode(line)
            except P.ProtocolError:
                continue                    # 校验不过丢弃，不回执不猜
            self.handle(f)
        now = time.monotonic()
        if now - self._last_status >= 0.05:            # 20 Hz
            self._last_status = now
            self._send_status()
        self._check_keepalive()
        self._pending = [(k, h) for k, h in self._pending
                         if self.chassis.poll(h).progress is ExecProgress.IN_PROGRESS]

    def serve_forever(self) -> None:
        while not self._stop.wait(0.005):
            self.step()

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self.stop()
        self.chassis.close()


# ---------------------------------------------------------------- PTY
class _PtyLink:
    """伪终端的一侧。用它就不需要 socat，也不需要两根 USB 转串口。

    **必须把伪终端设成 raw 模式。**默认的行规程会做两件要命的事：

    - **回显**：上位机写进从端的字节被原样回给主端，假小车于是收到自己发出
      的状态帧，判成 UNKNOWN_TYPE 再回一条 REJ，REJ 又被回显……链路瞬间被
      自激的帧刷满，真正的指令挤不进去。实测表现是 PAUSE 发出去车不停，而
      状态帧一直在正常上报——看起来像"指令丢了"，其实是链路被自己塞死了。
    - **ONLCR**：把 `\\n` 换成 `\\r\\n`。本协议靠 `\\n` 分帧，多出来的 `\\r`
      虽然会被 strip 掉，但 CRC 是按 `$` 与 `*` 之间的字节算的，一旦哪天在
      帧中间插进控制字符就会整片校验失败。

    真串口不存在这两条（USB 转串口设备本来就是 raw），所以这是伪终端仿真
    特有的坑，值得在这里写清楚。
    """

    def __init__(self) -> None:
        import termios
        import tty
        self.master, self.slave = os.openpty()
        tty.setraw(self.master)
        tty.setraw(self.slave)
        for fd in (self.master, self.slave):
            attrs = termios.tcgetattr(fd)
            attrs[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON)  # lflag
            attrs[1] &= ~termios.ONLCR                                      # oflag
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        os.set_blocking(self.master, False)
        self.name = os.ttyname(self.slave)

    def read(self) -> bytes:
        try:
            return os.read(self.master, 4096)
        except BlockingIOError:
            return b""
        except OSError:
            return b""

    def write(self, data: bytes) -> None:
        os.write(self.master, data)

    def close(self) -> None:
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


# ---------------------------------------------------------------- TCP 环回
class _TcpLink:
    """假小车这一侧的 TCP 环回端点。**Windows 上唯一可用的那条路。**

    ``os.openpty()`` 是 POSIX 专有的，Windows 上 ``hasattr(os, "openpty")``
    直接是 False，所以 _PtyLink 在 Windows 上连构造都构造不出来。没有硬件的
    时候，"真驱动 + 假硬件"这一层恰恰是唯一能证明**上位机侧代码是真的**
    （而不是桩里那条理想链路）的证据，所以它不能因为换个操作系统就没了。

    TCP 环回保住了真正要紧的那个性质：**假小车是独立进程，字节真的穿过内核**。
    分帧、CRC、超时、重传、2 % ACK 丢包注入全部照原样发生，换掉的只是承载。

    **会反复接受连接。**上位机重启时旧连接断开，这里自动回到 listen 等下一个，
    否则每演示一轮都得把假小车也重启一次——演示中途重启东西是很难看的。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5760):
        import socket
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, int(port)))
        self._srv.listen(1)
        # accept 也要超时，否则 step() 会被卡住，20 Hz 状态帧就发不出去了
        self._srv.settimeout(0.02)
        h, p = self._srv.getsockname()[:2]
        self.name = "tcp://%s:%d" % (h, p)
        self._conn = None
        self._lock = threading.Lock()
        self._closed = False

    def _accept_if_idle(self) -> None:
        if self._closed:
            return
        with self._lock:
            if self._conn is not None:
                return
        try:
            conn, _ = self._srv.accept()
        except OSError:              # 含超时：这一轮没人连，正常
            return
        conn.settimeout(0.01)
        with self._lock:
            self._conn = conn

    def _drop(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None

    def read(self) -> bytes:
        self._accept_if_idle()
        with self._lock:
            c = self._conn
        if c is None:
            return b""
        try:
            data = c.recv(4096)
        except OSError:              # 含超时：这一轮没数据
            return b""
        if not data:                 # recv 返回空 = 对端已关闭
            self._drop()
        return data

    def write(self, data: bytes) -> None:
        with self._lock:
            c = self._conn
        if c is None:
            return                   # 还没人连上，状态帧直接丢弃，和空串口一样
        try:
            c.sendall(data)
        except OSError:
            self._drop()

    def close(self) -> None:
        self._closed = True
        self._drop()
        try:
            self._srv.close()
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="假小车：说底盘串口协议的仿真底盘")
    ap.add_argument("--config", default=None)
    ap.add_argument("--pty", action="store_true", help="建伪终端（仅 POSIX）")
    ap.add_argument("--tcp", action="store_true",
                    help="用 TCP 环回代替伪终端。Windows 只能用这个")
    ap.add_argument("--port", type=int, default=5760, help="--tcp 的监听端口")
    # 默认用随机种子。固定种子加上大致固定的时序，会让 2 % 的丢包注入每次都
    # 落在同一条指令上——演示时表现为"PAUSE 每次都不生效"，看着像链路坏了，
    # 其实是桩在按设计丢包。要复现某次现象再用 --seed 固定。
    ap.add_argument("--seed", type=int, default=None,
                    help="随机种子。默认随机；给定后可复现同一串故障注入")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印收发的每一帧")
    a = ap.parse_args()

    cfg = Config.load(a.config)
    seed = a.seed if a.seed is not None else int.from_bytes(os.urandom(4), "little")

    # 选链路。显式指定优先；都没给就按平台自动挑——Windows 没有伪终端，
    # 自动落到 TCP，免得同学照着 README 敲 --pty 撞一脸 AttributeError。
    has_pty = hasattr(os, "openpty")
    if a.pty and not has_pty:
        print("本平台没有伪终端（os.openpty 不存在，多半是 Windows）。"
              "请改用：python -m patrol.tools.fakecar --tcp", file=sys.stderr)
        return 2
    use_tcp = a.tcp or (not a.pty and not has_pty)
    link = _TcpLink(port=a.port) if use_tcp else _PtyLink()

    car = FakeCar(cfg, link, seed=seed, verbose=a.verbose)
    print("随机种子 %d（--seed %d 可复现本次的故障注入序列）" % (seed, seed))
    print("假小车已就绪，链路：%s%s" % (link.name, "  [TCP 环回]" if use_tcp else "  [伪终端]"))
    print("把它填进 configs/real.yaml 的 real.serial.chassis.port，")
    print("并把 configs/system.yaml 的 driver_mode 改成 real。Ctrl-C 退出。")
    if use_tcp:
        print()
        print("  TCP 环回与真串口的差别（答辩时别说漏）：没有波特率、没有线路")
        print("  噪声、没有帧错误，且 TCP 保证有序不丢。所以它证明的是**协议栈")
        print("  与时序逻辑**正确，不证明物理层——物理层要等真串口。")
    try:
        car.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        car.close()
        link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
