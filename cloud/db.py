"""云端台账。SQLite，单文件、零运维，够一个实训课题用。

三张表对应任务书要求的"云端人工复核、模型版本管理和巡检结果闭环"：

    evidence      证据包元数据 + 复核结论 + 增益指标
    review        人工复核裁决（谁、什么时候、判成什么、理由）
    model_version 模型版本登记，把"哪一版模型产生了这条结论"钉住

**为什么裁决单独一张表而不是直接改 evidence**：边缘侧的判定与人工的裁决
是两个独立事实，覆盖掉前者就没法统计"AI 判对了多少"。分开存才能算出
误报率、漏报率这些真正要写进答辩材料的数。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
    event_id      TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    site_id       TEXT,
    waypoint_id   TEXT,
    ts_utc_ms     INTEGER NOT NULL,
    verdict       TEXT NOT NULL,
    defect_class  TEXT,
    severity      TEXT,
    needs_review  INTEGER NOT NULL DEFAULT 0,
    confidence    REAL,
    delta_conf    REAL,
    density_ratio REAL,
    verify_ok     INTEGER,
    aborted       INTEGER NOT NULL DEFAULT 0,
    abort_reason  TEXT,
    model_version TEXT,
    manifest      TEXT NOT NULL,
    received_ms   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ev_run  ON evidence(run_id);
CREATE INDEX IF NOT EXISTS idx_ev_ts   ON evidence(ts_utc_ms);
CREATE INDEX IF NOT EXISTS idx_ev_need ON evidence(needs_review);

CREATE TABLE IF NOT EXISTS review (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL,
    reviewer      TEXT NOT NULL,
    decision      TEXT NOT NULL,
    note          TEXT,
    ts_utc_ms     INTEGER NOT NULL,
    FOREIGN KEY(event_id) REFERENCES evidence(event_id)
);
CREATE INDEX IF NOT EXISTS idx_rv_ev ON review(event_id);

CREATE TABLE IF NOT EXISTS model_version (
    version       TEXT PRIMARY KEY,
    stage         TEXT NOT NULL,
    weights_sha   TEXT,
    dataset       TEXT,
    metrics       TEXT,
    note          TEXT,
    ts_utc_ms     INTEGER NOT NULL,
    active        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS asset (
    event_id      TEXT NOT NULL,
    name          TEXT NOT NULL,
    sha256        TEXT,
    bytes         INTEGER,
    ts_utc_ms     INTEGER NOT NULL,
    PRIMARY KEY(event_id, name)
);
"""


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            self._local.c = c
        return c

    # ------------------------------------------------------------ 写
    def upsert_evidence(self, manifest: dict, *, site_id: str | None,
                        received_ms: int, model_version: str | None = None) -> None:
        v = manifest.get("verdict", {})
        g = manifest.get("gain", {})
        ab = manifest.get("abort")
        row = (
            manifest["event_id"], manifest["run_id"], site_id,
            manifest.get("waypoint_id"), int(manifest.get("ts_utc_ms", 0)),
            v.get("result"), v.get("defect_class"), v.get("severity"),
            1 if v.get("needs_human_review") else 0, float(v.get("confidence", 0.0)),
            float(g.get("delta_conf", 0.0)), float(g.get("pixel_density_ratio", 0.0)),
            1 if g.get("verify_success") else 0,
            1 if ab else 0, (ab or {}).get("reason"), model_version,
            json.dumps(manifest, ensure_ascii=False), int(received_ms),
        )
        with self._conn() as c:
            c.execute("""INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(event_id) DO UPDATE SET
                           -- 补传/更正时这几个也要跟着更新。原来漏了
                           -- waypoint_id 与 ts_utc_ms，重传一份改正过的
                           -- manifest 之后台账里还留着旧的巡检位。
                           site_id=excluded.site_id,
                           waypoint_id=excluded.waypoint_id,
                           ts_utc_ms=excluded.ts_utc_ms,
                           model_version=excluded.model_version,
                           verdict=excluded.verdict, defect_class=excluded.defect_class,
                           severity=excluded.severity, needs_review=excluded.needs_review,
                           confidence=excluded.confidence, delta_conf=excluded.delta_conf,
                           density_ratio=excluded.density_ratio,
                           verify_ok=excluded.verify_ok, aborted=excluded.aborted,
                           abort_reason=excluded.abort_reason,
                           manifest=excluded.manifest, received_ms=excluded.received_ms""",
                      row)

    def record_asset(self, event_id: str, name: str, sha256: str | None,
                     nbytes: int, ts_utc_ms: int) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO asset VALUES (?,?,?,?,?)",
                      (event_id, name, sha256, int(nbytes), int(ts_utc_ms)))

    def add_review(self, event_id: str, reviewer: str, decision: str,
                   note: str, ts_utc_ms: int) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO review (event_id,reviewer,decision,note,ts_utc_ms)"
                " VALUES (?,?,?,?,?)",
                (event_id, reviewer, decision, note, int(ts_utc_ms)))
            c.execute("UPDATE evidence SET needs_review=0 WHERE event_id=?", (event_id,))
            return int(cur.lastrowid)

    def register_model(self, version: str, stage: str, *, weights_sha: str | None,
                       dataset: str | None, metrics: dict | None, note: str,
                       ts_utc_ms: int, activate: bool = False) -> None:
        with self._conn() as c:
            if activate:
                c.execute("UPDATE model_version SET active=0 WHERE stage=?", (stage,))
            c.execute("INSERT OR REPLACE INTO model_version VALUES (?,?,?,?,?,?,?,?)",
                      (version, stage, weights_sha, dataset,
                       json.dumps(metrics or {}, ensure_ascii=False), note,
                       int(ts_utc_ms), 1 if activate else 0))

    # ------------------------------------------------------------ 读
    def list_evidence(self, *, run_id: str | None = None,
                      needs_review: bool | None = None,
                      verdict: str | None = None, limit: int = 200) -> list[dict]:
        q = ("SELECT event_id,run_id,site_id,waypoint_id,ts_utc_ms,verdict,"
             "defect_class,severity,needs_review,confidence,delta_conf,"
             "density_ratio,verify_ok,aborted,abort_reason,model_version"
             " FROM evidence WHERE 1=1")
        args: list[Any] = []
        if run_id:
            q += " AND run_id=?"
            args.append(run_id)
        if needs_review is not None:
            q += " AND needs_review=?"
            args.append(1 if needs_review else 0)
        if verdict:
            q += " AND verdict=?"
            args.append(verdict)
        q += " ORDER BY ts_utc_ms DESC LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in self._conn().execute(q, args)]

    def get_evidence(self, event_id: str) -> dict | None:
        r = self._conn().execute("SELECT * FROM evidence WHERE event_id=?",
                                 (event_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["manifest"] = json.loads(d["manifest"])
        d["reviews"] = self.reviews_of(event_id)
        d["assets"] = [dict(a) for a in self._conn().execute(
            "SELECT name,sha256,bytes FROM asset WHERE event_id=? ORDER BY name",
            (event_id,))]
        return d

    def reviews_of(self, event_id: str) -> list[dict]:
        return [dict(r) for r in self._conn().execute(
            "SELECT reviewer,decision,note,ts_utc_ms FROM review"
            " WHERE event_id=? ORDER BY ts_utc_ms", (event_id,))]

    def models(self) -> list[dict]:
        return [dict(r) for r in self._conn().execute(
            "SELECT * FROM model_version ORDER BY ts_utc_ms DESC")]

    def runs(self) -> list[dict]:
        return [dict(r) for r in self._conn().execute(
            "SELECT run_id, COUNT(*) n, MIN(ts_utc_ms) t0, MAX(ts_utc_ms) t1"
            " FROM evidence GROUP BY run_id ORDER BY t1 DESC")]

    # ------------------------------------------------------------ 统计
    def gain_stats(self, run_id: str | None = None) -> dict:
        """复核增益指标。**必须按 verdict 分组。**

        ICD §6.4 特意警告过：delta_conf 对 FALSE_ALARM 会是负值（复核把一个
        0.41 的误检压到 0.05），混在一起算均值会接近零，看上去像是复核没起
        作用。这是做统计脚本时最容易搞错的地方。
        """
        where, args = ("WHERE run_id=?", [run_id]) if run_id else ("", [])
        rows = self._conn().execute(
            "SELECT verdict, COUNT(*) n, AVG(delta_conf) d, AVG(density_ratio) r,"
            " SUM(verify_ok) ok FROM evidence %s GROUP BY verdict" % where, args)
        by = {}
        tot = ok = 0
        for r in rows:
            by[r["verdict"]] = {"n": r["n"], "avg_delta_conf": round(r["d"] or 0.0, 4),
                                "avg_density_ratio": round(r["r"] or 0.0, 4),
                                "verify_ok": int(r["ok"] or 0)}
            tot += r["n"]
            ok += int(r["ok"] or 0)
        confirmed = by.get("CONFIRMED_DEFECT", {})
        abnormal = by.get("READING_ABNORMAL", {})
        real = [g for g in (confirmed, abnormal) if g]
        return {
            "by_verdict": by, "total": tot,
            "verify_success_rate": round(ok / tot, 4) if tot else 0.0,
            # 立论要证明的那个数：真缺陷组的 delta_conf 均值
            "delta_conf_on_real_defects": round(
                sum(g["avg_delta_conf"] * g["n"] for g in real)
                / max(1, sum(g["n"] for g in real)), 4) if real else None,
            "note": "delta_conf 必须按 verdict 分组统计；FALSE_ALARM 组为负值是正常的",
        }
