"""结构化日志。

每条日志带 run_id / event_id，这样事后按 event_id 过滤就能拿到一次复核的
完整时间线（ICD §2.2）。日志同时输出到控制台（人读）和 JSONL 文件（机读、
可回放）。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from patrol.common.clock import mono_ns, utc_ms

_LOCK = threading.Lock()
_CONTEXT = threading.local()

LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "WARNING": 30,
          "ERROR": 40, "CRITICAL": 50}


def set_context(**kw: Any) -> None:
    """设置本线程后续日志自动附带的字段，通常是 run_id / event_id。"""
    cur = getattr(_CONTEXT, "fields", {})
    merged = dict(cur)
    for k, v in kw.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    _CONTEXT.fields = merged


def get_context() -> dict:
    return dict(getattr(_CONTEXT, "fields", {}))


class JsonlSink:
    """把日志写成一行一条 JSON，供 replay.py 回放与统计脚本消费。"""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, record: dict) -> None:
        with _LOCK:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


class Logger:
    """节点日志器。node 名会出现在每条日志里，便于分辨四个进程。"""

    def __init__(self, node: str, level: str = "INFO",
                 sink: JsonlSink | None = None, stream=None):
        self.node = node
        self.level = LEVELS.get(str(level).upper(), 20)
        self.sink = sink
        self.stream = stream or sys.stderr

    def _emit(self, level: str, msg: str, **fields: Any) -> None:
        lv = LEVELS.get(level, 20)
        if lv < self.level:
            return
        rec = {
            "ts_utc_ms": utc_ms(),
            "ts_mono_ns": mono_ns(),
            "node": self.node,
            "level": level,
            "msg": msg,
        }
        rec.update(get_context())
        rec.update(fields)
        if self.sink is not None:
            self.sink.write(rec)
        tail = " ".join(
            "%s=%s" % (k, v) for k, v in fields.items() if k != "msg"
        )
        ev = rec.get("event_id")
        ev_s = " [%s]" % ev[:8] if ev else ""
        with _LOCK:
            self.stream.write(
                "%-5s %-10s%s %s%s\n"
                % (level, self.node, ev_s, msg, (" " + tail) if tail else "")
            )
            self.stream.flush()

    def debug(self, msg: str, **f: Any) -> None:  self._emit("DEBUG", msg, **f)
    def info(self, msg: str, **f: Any) -> None:   self._emit("INFO", msg, **f)
    def warn(self, msg: str, **f: Any) -> None:   self._emit("WARN", msg, **f)
    def error(self, msg: str, **f: Any) -> None:  self._emit("ERROR", msg, **f)
    def critical(self, msg: str, **f: Any) -> None: self._emit("CRITICAL", msg, **f)


def build_logger(node: str, cfg=None, run_id: str | None = None) -> Logger:
    level = "INFO"
    log_dir = "logs"
    if cfg is not None:
        level = cfg.get("logging.level", "INFO")
        log_dir = cfg.get("logging.dir", "logs")
    sink = None
    if log_dir:
        name = "%s.jsonl" % node if not run_id else "%s-%s.jsonl" % (run_id, node)
        sink = JsonlSink(Path(log_dir) / name)
    lg = Logger(node, level=level, sink=sink)
    if run_id:
        set_context(run_id=run_id)
    return lg


@contextmanager
def fatal_guard(node: str, cfg=None):
    """节点主循环外面套一层：未处理的异常先把完整 traceback 写进 logs/<node>.jsonl，再原样抛出。

    进程照旧以非零码退出，run_all 的横幅照旧打；区别只在于事后能在日志里查到死因。
    原先异常只打到终端，批量跑或挂长稳时终端早就翻过去了。
    Ctrl-C 与 SystemExit 是正常退出路径，不记。
    """
    try:
        yield
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:  # noqa: BLE001
        try:
            lg = build_logger(node, cfg)
            lg.critical("进程异常退出", exc_type=type(e).__name__, exc=str(e)[:500],
                        traceback=traceback.format_exc())
            if lg.sink is not None:
                lg.sink.close()
        except Exception:  # noqa: BLE001
            pass
        raise


# logging 模块桥接：第三方库（uvicorn 等）的日志也进同一个流
def quiet_third_party(names=("uvicorn", "uvicorn.access", "asyncio")) -> None:
    for n in names:
        logging.getLogger(n).setLevel(logging.WARNING)
