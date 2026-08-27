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

    @app.put("/api/evidence/{run_id}/{event_id}/files/{name}")
    async def put_file(run_id: str, event_id: str, name: str, req: Request):
        if "/" in name or ".." in name:
            raise HTTPException(400, "非法文件名")
        data = await req.body()
        want = req.headers.get("X-Sha256")
        got = hashlib.sha256(data).hexdigest()
        if want and want != got:
            # 哈希不一致说明传输出错，拒收让边缘侧重传
            raise HTTPException(409, "sha256 不一致: 期望 %s 实得 %s" % (want, got))
        d = storage / run_id / event_id
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
        ledger.record_asset(event_id, name, got, len(data), int(time.time() * 1000))
        return {"ok": True, "bytes": len(data), "sha256": got}

    # -------------------------------------------------------- 查询
    @app.get("/api/evidence")
    def list_evidence(run_id: str | None = None, needs_review: bool | None = None,
                      verdict: str | None = None, limit: int = 200):
        return ledger.list_evidence(run_id=run_id, needs_review=needs_review,
                                    verdict=verdict, limit=limit)

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
        p = storage / run_id / event_id / name
        if not p.exists():
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
