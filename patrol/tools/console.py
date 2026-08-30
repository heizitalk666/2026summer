"""指令实时流：把下发给小车和云台的每一条指令直接显示出来。

**没有硬件的时候，这就是"车"和"云台"唯一看得见的样子。**

数据源是网关的审计日志 `logs/gateway-audit.jsonl`，不是总线。这个选择是有
理由的：

- IF-2 是 REQ/REP，只有网关一个人收得到，旁听不了。要旁听就得给网关加一路
  PUB，那是改接口——ICD 冻结了四条接口，为了一个显示工具去动它不划算
- 审计日志记的是**校验之后**的结果：指令、参数、ACK、拒绝码、六项 checks
  逐项通过还是失败、以及处理耗时。这些正是要显示的东西，而且是权威版本
- 网关每条都 flush，所以 tail 得到的就是实时流；工具崩了重开也不丢历史

三个显示面各取所需，但**只有这一个进程订阅总线**：

    终端     直接打印（默认）
    网页     --push 推给云端的 /api/live/push，浏览器上「实时」页看
    预览窗   viewer --live 自己订阅 IF-3，不经过这里

用法：

    python -m patrol.tools.console                     # 跟着最新的看
    python -m patrol.tools.console --from-start        # 从头回放一遍
    python -m patrol.tools.console --heartbeat         # 连心跳一起显示
    python -m patrol.tools.console --push http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from patrol.common.config import Config

# ---------------------------------------------------------------- 配色
#: 终端色。不是好看，是**让被拒的指令一眼就能挑出来**——安全边界起没起作用，
#: 演示时全靠这一行红字，混在几百行白字里等于没有。
_C = {"reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
      "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
      "blue": "\033[34m", "cyan": "\033[36m", "magenta": "\033[35m"}


def _color_enabled(stream=sys.stdout) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(text: str, *names: str, enabled: bool = True) -> str:
    if not enabled or not names:
        return text
    return "".join(_C[n] for n in names if n in _C) + text + _C["reset"]


# ---------------------------------------------------------------- 翻译
#: 指令 → (下给谁, 人话动词)。六条白名单之外的东西不会出现在这里——
#: 网关会先把它拒掉，那种情况走 reject 分支显示。
_VERB = {
    "PAUSE": ("小车", "暂停"),
    "RESUME": ("小车", "恢复巡航"),
    "CREEP_FORWARD": ("小车", "蠕动前进"),
    "GOTO_OBSERVE": ("小车", "去观察位"),
    "PTZ_SET": ("云台", "转到"),
    "PTZ_RATE": ("云台", "转速"),
    "HEARTBEAT": ("心跳", ""),
}
_TARGET_COLOR = {"小车": "cyan", "云台": "magenta", "心跳": "dim"}


def _fmt_params(command: str, params: dict) -> str:
    """把参数写成人看的样子。**单位一律显式写出来。**

    调试云台时最容易犯的错就是把弧度当成度、或者把 zoom 当成焦距。
    参数表里写着 `2.4` 谁也说不清是什么，写成 `zoom=2.4×` 就没有歧义。
    """
    p = params or {}
    if command == "PTZ_SET":
        bits = []
        for k, unit in (("pan_deg", "°"), ("tilt_deg", "°")):
            if p.get(k) is not None:
                bits.append("%s=%+.1f%s" % (k.split("_")[0], float(p[k]), unit))
        if p.get("zoom") is not None:
            bits.append("zoom=%.2f×" % float(p["zoom"]))
        if p.get("speed"):
            bits.append(str(p["speed"]).lower())
        return " ".join(bits)
    if command == "PTZ_RATE":
        return " ".join("%s=%+.1f°/s" % (k.split("_")[0], float(v))
                        for k, v in p.items() if k.endswith("_dps"))
    if command == "CREEP_FORWARD":
        return "%.2f m" % float(p.get("distance_m", 0.0))
    if command == "GOTO_OBSERVE":
        return "(%.2f, %.2f) yaw=%+.1f°" % (
            float(p.get("x_m", 0.0)), float(p.get("y_m", 0.0)),
            float(p.get("yaw_deg", 0.0)))
    if command == "PAUSE":
        return str(p.get("reason", ""))
    if command == "HEARTBEAT":
        return str(p.get("mission_state", ""))
    return json.dumps(p, ensure_ascii=False) if p else ""


def failed_checks(rec: dict) -> list[str]:
    """哪几项校验没过。**这是安全边界唯一可观测的地方。**

    ICD §4.6 要求网关把六项检查的结果逐项上报，正是为了让"有没有在校验"
    这件事看得见。指令被拒时不显示是哪一项拒的，等于把这个设计浪费掉。
    """
    return sorted(k for k, v in (rec.get("checks") or {}).items()
                  if str(v).upper() == "FAIL")


@dataclass
class Line:
    """一条待显示的指令。web 面板与终端共用这一份，避免两处翻译分叉。"""

    ts_utc_ms: int
    target: str
    text: str
    ok: bool
    latency_ms: float
    detail: str = ""
    command: str = ""
    event_id: str | None = None

    def as_dict(self) -> dict:
        return {"ts_utc_ms": self.ts_utc_ms, "target": self.target,
                "text": self.text, "ok": self.ok,
                "latency_ms": round(self.latency_ms, 1), "detail": self.detail,
                "command": self.command, "event_id": self.event_id}


def describe(rec: dict) -> Line:
    """审计记录 → 一行人话。"""
    cmd = str(rec.get("command", "?"))
    target, verb = _VERB.get(cmd, ("未知", cmd))
    params = _fmt_params(cmd, rec.get("params") or {})
    text = ("%s %s" % (verb, params)).strip() or cmd
    ok = str(rec.get("result", "")).upper() == "ACCEPTED"
    detail = ""
    if not ok:
        bad = failed_checks(rec)
        detail = "%s %s" % (rec.get("reject_code") or "REJECTED",
                            rec.get("reject_detail") or "")
        if bad:
            detail += "（未过：%s）" % "、".join(bad)
    return Line(ts_utc_ms=int(rec.get("ts_utc_ms", 0)), target=target, text=text,
                ok=ok, latency_ms=float(rec.get("handle_us", 0)) / 1000.0,
                detail=detail.strip(), command=cmd,
                event_id=rec.get("event_id"))


def render(line: Line, *, t0_ms: int, color: bool = True) -> str:
    """一行终端输出。列宽固定，让眼睛能沿着列往下扫。"""
    t = (line.ts_utc_ms - t0_ms) / 1000.0 if t0_ms else 0.0
    mark = paint("✓", "green", enabled=color) if line.ok \
        else paint("✗", "red", "bold", enabled=color)
    tgt = paint("%-4s" % line.target,
                _TARGET_COLOR.get(line.target, "reset"), enabled=color)
    body = "%-46s" % line.text
    if not line.ok:
        body = paint(body, "red", enabled=color)
    lat = paint("%6.1f ms" % line.latency_ms, "dim", enabled=color)
    out = "[t=%7.1fs] → %s %s %s %s" % (t, tgt, mark, body, lat)
    if line.detail:
        out += "  " + paint(line.detail, "red", enabled=color)
    return out


# ---------------------------------------------------------------- 跟读
class AuditTail:
    """跟读审计日志。文件还没建、被截断、被换掉都要能自愈。

    三条行为，每条都对应演示时真会遇到的一种情况：

    1. **文件还不存在** —— 顺序基本一定是"先开控制台，再开 run_all"。
       这时不该报错退出，而该等着；等它出现之后，**里面的内容全都是新的**，
       所以从头读，而不是跳到末尾（跳到末尾会把这一轮开头那几条指令吞掉）。
    2. **文件已经存在** —— 上一轮的历史，默认跳过只看新的，`from_start` 要
       回放才读。
    3. **文件被换掉或截断** —— 换 run_id、手工清日志。重新跟上，并且在**同
       一次 poll 里**就把新内容读出来，不拖到下一拍。
    """

    def __init__(self, path: str | os.PathLike, *, from_start: bool = False):
        self.path = Path(path)
        # 构造时文件就不存在的话，它将来出现时里面的每一条都是新的
        self.from_start = bool(from_start) or not self.path.exists()
        self._fh = None
        self._inode = None

    def _open(self) -> bool:
        try:
            fh = open(self.path, "r", encoding="utf-8")
        except OSError:
            return False
        st = os.fstat(fh.fileno())
        if not self.from_start:
            fh.seek(0, os.SEEK_END)
        self._fh, self._inode = fh, (st.st_dev, st.st_ino)
        return True

    def _read(self) -> list[dict]:
        out: list[dict] = []
        if self._fh is None:
            return out
        for raw in self._fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue                      # 半行：下次 poll 会重新读到完整的
        return out

    def _rotated(self) -> bool:
        try:
            st = os.stat(self.path)
        except OSError:
            return False
        if (st.st_dev, st.st_ino) != self._inode:
            return True
        return self._fh is not None and st.st_size < self._fh.tell()

    def poll(self) -> list[dict]:
        """取出自上次调用以来的新记录。**不阻塞。**"""
        if self._fh is None and not self._open():
            return []
        out = self._read()
        if self._rotated():
            self.close()
            self.from_start = True            # 换了文件，新文件整份都是新的
            if self._open():
                out += self._read()
        return out

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
        self._fh = None


# ---------------------------------------------------------------- 状态
@dataclass
class Snapshot:
    """最近一次 IF-3。终端底部的状态行与网页俯视图都用它。"""

    ts_utc_ms: int = 0
    x_m: float = 0.0
    y_m: float = 0.0
    yaw_deg: float = 0.0
    speed_mps: float = 0.0
    chassis: str = "-"
    waypoint_id: str | None = None
    pan_deg: float = 0.0
    tilt_deg: float = 0.0
    zoom: float = 1.0
    hfov_deg: float = 60.0
    battery_pct: float = 0.0
    safety_active: bool = False
    mission_state: str = "-"
    detections: list = field(default_factory=list)

    def update_status(self, st: dict) -> None:
        pose, ptz, ch = st.get("pose") or {}, st.get("ptz") or {}, st.get("chassis") or {}
        self.ts_utc_ms = int(st.get("ts_utc_ms", self.ts_utc_ms))
        self.x_m = float(pose.get("x_m", self.x_m))
        self.y_m = float(pose.get("y_m", self.y_m))
        self.yaw_deg = float(pose.get("yaw_deg", self.yaw_deg))
        self.speed_mps = float(ch.get("speed_mps", self.speed_mps))
        self.chassis = str(ch.get("state", self.chassis))
        self.waypoint_id = ch.get("current_waypoint_id", self.waypoint_id)
        self.battery_pct = float(ch.get("battery_pct", self.battery_pct))
        self.safety_active = bool(ch.get("safety_layer_active", False))
        self.pan_deg = float(ptz.get("pan_deg", self.pan_deg))
        self.tilt_deg = float(ptz.get("tilt_deg", self.tilt_deg))
        self.zoom = float(ptz.get("zoom", self.zoom))
        self.hfov_deg = float(ptz.get("hfov_deg", self.hfov_deg))

    def update_detection(self, ev: dict) -> None:
        """四路模型这一刻在说什么。只留最必要的几项，网页要每秒刷。"""
        self.detections = [
            {"defect_class": d.get("defect_class"),
             "confidence": round(float(d.get("confidence", 0.0)), 3),
             "pixel_density_px": round(float(d.get("pixel_density_px", 0.0)), 1),
             "l2": (d.get("l2_reading") or {}).get("value"),
             "unit": (d.get("l2_reading") or {}).get("unit"),
             "in_band": (d.get("l2_reading") or {}).get("in_normal_band")}
            for d in (ev.get("detections") or [])[:6]]

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["detections"] = list(self.detections)
        return d


def status_line(s: Snapshot, *, color: bool = True) -> str:
    hz = "%.1f°" % s.hfov_deg
    body = ("车 (%.2f, %.2f) yaw=%+.0f°  %s %.2f m/s  %s │ "
            "云台 pan=%+.1f° tilt=%+.1f° zoom=%.2f× 视场 %s │ 电量 %.0f%%"
            % (s.x_m, s.y_m, s.yaw_deg, s.chassis, s.speed_mps,
               s.waypoint_id or "-", s.pan_deg, s.tilt_deg, s.zoom, hz,
               s.battery_pct))
    if s.safety_active:
        body += paint("  ⚠ 安全层已介入", "red", "bold", enabled=color)
    return paint(body, "dim", enabled=color) if not s.safety_active else body


# ---------------------------------------------------------------- 主循环
class Console:
    """把审计日志与 IF-3/IF-1 汇成一个显示流。

    **它是只读的：不发指令、不改状态、不参与任何判决。**多开几个、开在跑到
    一半的时候、跑挂了重开，对系统都没有任何影响——演示时这一点很重要。
    """

    def __init__(self, cfg: Config, *, from_start: bool = False,
                 show_heartbeat: bool = False, color: bool | None = None,
                 push_url: str | None = None):
        self.cfg = cfg
        self.tail = AuditTail(cfg.get("gateway.audit_log",
                                      "logs/gateway-audit.jsonl"),
                              from_start=from_start)
        self.show_heartbeat = bool(show_heartbeat)
        self.color = _color_enabled() if color is None else bool(color)
        self.push_url = push_url.rstrip("/") if push_url else None
        self.snap = Snapshot()
        self.t0_ms = 0
        self.lines = 0
        self.rejected = 0
        self._sub_status = None
        self._sub_det = None
        self._recent: list[Line] = []

    # -------------------------------------------------------------- 总线
    def open_bus(self) -> None:
        from patrol.common.bus import Subscriber
        self._sub_status = Subscriber(self.cfg.get("bus.status"),
                                      topics=["STATUS_REPORT"])
        self._sub_det = Subscriber(self.cfg.get("bus.detection"),
                                   topics=["DETECTION_EVENT"])

    def close(self) -> None:
        self.tail.close()
        for s in (self._sub_status, self._sub_det):
            if s is not None:
                try:
                    s.close()
                except Exception:                              # noqa: BLE001
                    pass

    # -------------------------------------------------------------- 一拍
    def step(self) -> list[Line]:
        """读一轮、返回本轮新产生的显示行。**不阻塞、不睡。**"""
        for s, fn in ((self._sub_status, self.snap.update_status),
                      (self._sub_det, self.snap.update_detection)):
            if s is None:
                continue
            for msg in s.drain(max_n=200):
                try:
                    fn(msg)
                except (TypeError, ValueError, KeyError):
                    continue
        out: list[Line] = []
        for rec in self.tail.poll():
            if rec.get("command") == "HEARTBEAT" and not self.show_heartbeat:
                # 心跳 5 Hz，一轮巡检三千多条。它证明的是"看门狗活着"，
                # 混在指令流里只会把真正的指令淹掉。
                if str(rec.get("result", "")).upper() == "ACCEPTED":
                    continue
            line = describe(rec)
            if self.t0_ms == 0:
                self.t0_ms = line.ts_utc_ms
            self.lines += 1
            self.rejected += (not line.ok)
            out.append(line)
        if out:
            self._recent = (self._recent + out)[-200:]
        return out

    def push(self, lines: list[Line]) -> None:
        """把这一轮推给云端。**推不上去就算了，绝不能反过来卡住显示。**"""
        if not self.push_url:
            return
        try:
            import requests
            requests.post(self.push_url + "/api/live/push",
                          json={"commands": [l.as_dict() for l in lines],
                                "snapshot": self.snap.as_dict()},
                          timeout=0.8)
        except Exception:                                      # noqa: BLE001
            pass

    def run(self, *, seconds: float | None = None, period_s: float = 0.2) -> int:
        self.open_bus()
        print(paint("指令实时流  ——  按 Ctrl-C 退出", "bold", enabled=self.color))
        print(paint("源：%s%s" % (self.tail.path,
                                 "" if not self.push_url
                                 else "   推送：" + self.push_url),
                    "dim", enabled=self.color))
        print(paint("─" * 108, "dim", enabled=self.color))
        t_end = None if seconds is None else time.monotonic() + float(seconds)
        last_status = 0.0
        try:
            while t_end is None or time.monotonic() < t_end:
                new = self.step()
                for line in new:
                    print(render(line, t0_ms=self.t0_ms, color=self.color))
                self.push(new)
                now = time.monotonic()
                if self.snap.ts_utc_ms and now - last_status >= 2.0:
                    print(status_line(self.snap, color=self.color))
                    last_status = now
                time.sleep(period_s)
        except KeyboardInterrupt:
            pass
        finally:
            self.close()
        print(paint("─" * 108, "dim", enabled=self.color))
        print("共 %d 条指令，其中被拒 %d 条" % (self.lines, self.rejected))
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="把下发给小车和云台的指令实时显示出来")
    ap.add_argument("--audit", default=None, help="审计日志路径（默认取配置）")
    ap.add_argument("--from-start", action="store_true",
                    help="从头回放，而不是只看新的")
    ap.add_argument("--heartbeat", action="store_true", help="连心跳一起显示")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--push", default=None,
                    help="把同一批事件推给云端，例如 http://127.0.0.1:8000")
    ap.add_argument("--seconds", type=float, default=None)
    a = ap.parse_args(argv)
    over = {} if not a.audit else {"gateway": {"audit_log": a.audit}}
    cfg = Config.load(overrides=over)
    return Console(cfg, from_start=a.from_start, show_heartbeat=a.heartbeat,
                   color=False if a.no_color else None,
                   push_url=a.push).run(seconds=a.seconds)


if __name__ == "__main__":
    raise SystemExit(main())
