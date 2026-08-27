#!/usr/bin/env python3
"""云端接收与台账服务。

    python -m cloud.server            # 默认 127.0.0.1:8000

三条职责，对应任务书的"云端人工复核、模型版本管理和巡检结果闭环"：

  POST /api/evidence                       接收 manifest（元数据先到）
  PUT  /api/evidence/{run}/{ev}/files/{n}  接收文件，按 sha256 校验
  POST /api/evidence/{ev}/review           人工复核裁决
  POST /api/models                         模型版本登记
  GET  /                                   台账网页

**先收元数据再收文件**：断网恢复后即使文件还没传完，云端已经知道发生过
什么、结论是什么。告警的时效性由元数据保证，图片是事后佐证。

文件按 sha256 校验并去重：同一个 sha 只落一份，重传不会写坏已有文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import deque
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cloud.db import Ledger
from patrol.common import messages as M
from patrol.common.config import Config

WEB = Path(__file__).resolve().parent / "web"


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config.load()
    db_path = cfg.get("cloud.db", "cloud/patrol.db")
    storage = Path(cfg.get("cloud.storage", "cloud/storage"))
    storage.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(db_path)

    app = FastAPI(title="配电室巡检云端台账", version="1.0")
    app.state.ledger = ledger
    app.state.storage = storage

    # -------------------------------------------------------- 接收
    @app.post("/api/evidence")
    async def post_evidence(req: Request):
        body = await req.json()
        manifest = body.get("manifest") or body
        try:
            M.validate(manifest, "EVIDENCE_PACKAGE")
        except M.SchemaViolation as e:
            # 不合法的报文直接拒收，不入库。台账里的每一条都必须是合规的。
            raise HTTPException(422, "manifest 不合规: %s" % e) from e
        ledger.upsert_evidence(manifest, site_id=body.get("site_id"),
                               received_ms=int(time.time() * 1000),
                               model_version=body.get("model_version"))
        return {"ok": True, "event_id": manifest["event_id"],
                "files_expected": len(manifest.get("files", []))}

    #: 单个证据文件的上限。cruise_clip.mp4 通常几百 KB，留足余量即可。
    #: 没有上限的话一个坏客户端就能把云端的内存吃光（req.body() 是全量读进来的）。
    max_file_bytes = int(cfg.get("cloud.max_file_bytes", 64 * 1024 * 1024))

    def safe_path(*parts: str) -> Path:
        """把 URL 里来的路径片段拼进 storage，并**确认没有跑出去**。

        `storage / run_id / event_id / name` 这种写法，只要哪一段是 `..`
        就能读写 storage 之外的任意文件。原来只校验了 name 一段，run_id 与
        event_id 完全没查——而它们同样来自 URL。这里统一 resolve 之后比较
        前缀，比逐段过滤黑名单可靠（`%2e%2e`、`.%2e` 之类的变体绕不过去）。
        """
        for x in parts:
            if not x or x in (".", "..") or "/" in x or "\\" in x or "\x00" in x:
                raise HTTPException(400, "非法路径片段: %r" % x)
        root = storage.resolve()
        p = (root.joinpath(*parts)).resolve()
        if p != root and root not in p.parents:
            raise HTTPException(400, "路径越界")
        return p

    @app.put("/api/evidence/{run_id}/{event_id}/files/{name}")
    async def put_file(run_id: str, event_id: str, name: str, req: Request):
        target = safe_path(run_id, event_id, name)
        data = await req.body()
        if len(data) > max_file_bytes:
            raise HTTPException(413, "文件超过 %d 字节上限" % max_file_bytes)
        want = req.headers.get("X-Sha256")
        got = hashlib.sha256(data).hexdigest()
        if want and want != got:
            # 哈希不一致说明传输出错，拒收让边缘侧重传
            raise HTTPException(409, "sha256 不一致: 期望 %s 实得 %s" % (want, got))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        ledger.record_asset(event_id, name, got, len(data), int(time.time() * 1000))
        return {"ok": True, "bytes": len(data), "sha256": got}

    # -------------------------------------------------------- 查询
    @app.get("/api/evidence")
    def list_evidence(run_id: str | None = None, needs_review: bool | None = None,
                      verdict: str | None = None, limit: int = 200):
        return ledger.list_evidence(run_id=run_id, needs_review=needs_review,
                                    verdict=verdict,
                                    limit=max(1, min(int(limit), 2000)))

    @app.get("/api/evidence/{event_id}")
    def get_evidence(event_id: str):
        d = ledger.get_evidence(event_id)
        if d is None:
            raise HTTPException(404, "没有这条证据包")
        return d

    @app.get("/api/runs")
    def runs():
        return ledger.runs()

    @app.get("/api/stats")
    def stats(run_id: str | None = None):
        return ledger.gain_stats(run_id)

    @app.get("/api/files/{run_id}/{event_id}/{name}")
    def get_file(run_id: str, event_id: str, name: str):
        p = safe_path(run_id, event_id, name)      # 这里原来一段都没校验
        if not p.is_file():
            raise HTTPException(404, "文件不存在")
        return FileResponse(p)

    # -------------------------------------------------------- 人工复核
    @app.post("/api/evidence/{event_id}/review")
    async def review(event_id: str, req: Request):
        body = await req.json()
        decision = str(body.get("decision", "")).upper()
        allowed = {"CONFIRM", "REJECT", "DEFECT", "FALSE_ALARM", "NEED_MORE"}
        if decision not in allowed:
            raise HTTPException(400, "decision 必须是 %s 之一" % sorted(allowed))
        if ledger.get_evidence(event_id) is None:
            raise HTTPException(404, "没有这条证据包")
        rid = ledger.add_review(event_id, str(body.get("reviewer", "anonymous")),
                                decision, str(body.get("note", "")),
                                int(time.time() * 1000))
        return {"ok": True, "review_id": rid}

    # -------------------------------------------------------- 模型版本
    @app.post("/api/models")
    async def register_model(req: Request):
        b = await req.json()
        if not b.get("version") or not b.get("stage"):
            raise HTTPException(400, "version 与 stage 必填")
        ledger.register_model(str(b["version"]), str(b["stage"]),
                              weights_sha=b.get("weights_sha"),
                              dataset=b.get("dataset"), metrics=b.get("metrics"),
                              note=str(b.get("note", "")),
                              ts_utc_ms=int(time.time() * 1000),
                              activate=bool(b.get("activate", False)))
        return {"ok": True}

    @app.get("/api/models")
    def list_models():
        return ledger.models()

    # -------------------------------------------------------- 实时遥测
    #
    # **这一路数据不落库、不进台账，只在内存里留最近一段。**
    #
    # 云端的职责是"事后可查"——证据包、裁决、模型版本，这些都要持久化。
    # 实时指令流是"当场可见"，性质完全不同：它每秒几十条、没有留存价值、
    # 而且断了重连就该从当前状态接着看，不该补历史。把它写进 SQLite 只会
    # 让台账库被无用数据撑爆，还会让"库里有什么"这件事变得说不清。
    #
    # 推送方是 patrol/tools/console.py（唯一订阅总线的显示进程）。云端在
    # 这里只做一件事：接住、留最近 N 条、给网页取。车不在线时这一路自然
    # 就是空的，台账照常能看。
    live: dict = {"commands": deque(maxlen=int(cfg.get("cloud.live_ring", 400))),
                  "snapshot": {}, "updated_ms": 0}

    @app.post("/api/live/push")
    async def live_push(req: Request):
        body = await req.json()
        cmds = body.get("commands") or []
        if not isinstance(cmds, list):
            raise HTTPException(status_code=400, detail="commands 必须是数组")
        for c in cmds[:200]:
            if isinstance(c, dict):
                live["commands"].append(c)
        snap = body.get("snapshot")
        if isinstance(snap, dict):
            live["snapshot"] = snap
        live["updated_ms"] = int(time.time() * 1000)
        return {"ok": True, "buffered": len(live["commands"])}

    @app.get("/api/live/state")
    def live_state(after_ms: int = 0, limit: int = 120):
        """网页轮询这一个端点。

        用轮询而不是 SSE：本地演示 2 Hz 轮询和推流看不出差别，而 SSE 要引入
        异步生成器与连接生命周期管理，多出来的复杂度全落在"演示时它别崩"这
        件最不该冒险的事上。`after_ms` 让网页只取自己没见过的那几条。
        """
        lim = max(1, min(int(limit), 400))
        cmds = [c for c in live["commands"]
                if int(c.get("ts_utc_ms", 0)) > int(after_ms)][-lim:]
        stale_s = ((int(time.time() * 1000) - live["updated_ms"]) / 1000.0
                   if live["updated_ms"] else None)
        return {"commands": cmds, "snapshot": live["snapshot"],
                "updated_ms": live["updated_ms"],
                # 网页据此显示"车在线 / 已 12 s 没有数据"，比静默停更新强
                "stale_s": None if stale_s is None else round(stale_s, 1)}

    @app.get("/api/live/map")
    def live_map():
        """俯视图的静态底图：过道折线、航点、柜面上的目标。

        车与云台每秒动几十次，这些一轮里一次都不动，所以分开取一次就够——
        每次轮询都把它们捎上是白费带宽，也让轮询响应大得没必要。
        """
        wps = [{"id": w.get("id"), "x_m": w.get("x_m"), "y_m": w.get("y_m")}
               for w in (cfg.get("waypoints") or [])]
        tgts = []
        for t in (cfg.get("scene.targets") or []):
            pos = t.get("position") or {}
            tgts.append({"id": t.get("id"), "x_m": pos.get("x_m"),
                         "y_m": pos.get("y_m"),
                         "kind": (t.get("truth") or {}).get("kind")})
        return {"waypoints": wps, "targets": tgts,
                "route": cfg.get("scene.route.points") or []}

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # -------------------------------------------------------- 网页
    if WEB.exists():
        app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")

        @app.get("/", response_class=HTMLResponse)
        def index():
            return (WEB / "index.html").read_text(encoding="utf-8")

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description="云端接收与台账")
    ap.add_argument("--config", default=None)
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    a = ap.parse_args()
    cfg = Config.load(a.config)
    import uvicorn
    uvicorn.run(create_app(cfg), host=a.host or cfg.get("cloud.host", "127.0.0.1"),
                port=a.port or int(cfg.get("cloud.port", 8000)), log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
