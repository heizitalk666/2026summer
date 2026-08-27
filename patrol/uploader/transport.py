"""证据包上传。ICD §6.6。

| 环节   | 方式                                                      |
|--------|-----------------------------------------------------------|
| 元数据 | MQTT topic patrol/<site_id>/<run_id>/evidence，QoS 1      |
| 文件   | HTTPS PUT，URL 由云端在响应中签发，按 sha256 去重         |
| 断网   | 边缘落盘保留，uploaded = false，恢复后按 ts_utc_ms 由旧到新补传 |
| 重传   | 单文件 5 次，仍失败标记 UPLOAD_FAILED 并保留本地          |

**先传元数据再传文件**：断网恢复后即使文件还没传完，云端已经知道发生过
什么、结论是什么。告警的时效性由元数据保证，图片是事后佐证。

默认走 HTTP（单机零依赖就能跑通），配置里可切到 MQTT 接 mosquitto。
两条通路发的是同一份 manifest，云端接收逻辑不区分。
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import requests


@dataclass
class UploadResult:
    ok: bool
    uploaded: list[str]
    failed: list[str]
    error: str | None = None


class ITransport(ABC):
    @abstractmethod
    def send_manifest(self, manifest: dict) -> bool: ...

    @abstractmethod
    def put_file(self, run_id: str, event_id: str, path: Path,
                 sha256: str) -> bool: ...

    def close(self) -> None:
        return None


class HttpTransport(ITransport):
    def __init__(self, cfg):
        self.base = str(cfg.get("uploader.cloud_url")).rstrip("/")
        self.site = str(cfg.get("uploader.site_id", "SITE-01"))
        self.timeout = float(cfg.get("uploader.http_timeout_s", 5.0))

    def send_manifest(self, manifest: dict) -> bool:
        r = requests.post("%s/api/evidence" % self.base,
                          json={"site_id": self.site, "manifest": manifest},
                          timeout=self.timeout)
        return r.status_code < 300

    def put_file(self, run_id: str, event_id: str, path: Path, sha256: str) -> bool:
        with open(path, "rb") as f:
            r = requests.put(
                "%s/api/evidence/%s/%s/files/%s" % (self.base, run_id, event_id, path.name),
                data=f, headers={"X-Sha256": sha256,
                                 "Content-Type": "application/octet-stream"},
                timeout=self.timeout * 4)
        return r.status_code < 300


class MqttTransport(ITransport):
    """元数据走 MQTT，文件仍走 HTTPS PUT（ICD §6.6 就是这么分的）。"""

    def __init__(self, cfg):
        import paho.mqtt.client as mqtt
        m = cfg.get("uploader.mqtt")
        self.topic_tpl = str(m.get("topic_tpl", "patrol/{site_id}/{run_id}/evidence"))
        self.qos = int(m.get("qos", 1))
        self.site = str(cfg.get("uploader.site_id", "SITE-01"))
        self._http = HttpTransport(cfg)
        self._c = mqtt.Client()
        self._c.connect(str(m.get("host", "127.0.0.1")), int(m.get("port", 1883)), 30)
        self._c.loop_start()

    def send_manifest(self, manifest: dict) -> bool:
        topic = self.topic_tpl.format(site_id=self.site, run_id=manifest["run_id"])
        payload = json.dumps(manifest, ensure_ascii=False)
        if len(payload.encode("utf-8")) > 16384:
            # ICD §6.6：单条元数据 < 16 KB
            return False
        info = self._c.publish(topic, payload, qos=self.qos)
        info.wait_for_publish(timeout=5.0)
        return info.is_published()

    def put_file(self, run_id: str, event_id: str, path: Path, sha256: str) -> bool:
        return self._http.put_file(run_id, event_id, path, sha256)

    def close(self) -> None:
        try:
            self._c.loop_stop()
            self._c.disconnect()
        except Exception:            # noqa: BLE001
            pass


def build_transport(cfg) -> ITransport:
    kind = str(cfg.get("uploader.transport", "http")).lower()
    if kind == "mqtt":
        return MqttTransport(cfg)
    return HttpTransport(cfg)


class UploadQueue:
    """断点续传。

    只有收到云端回执并校验哈希一致后，本地记录才转为已确认；此时才允许被
    磁盘水位管理删除。**未确认的数据永不自动删除**（方案书 §8.3.5）。
    """

    def __init__(self, cfg, transport: ITransport | None = None):
        self.root = Path(cfg.get("uploader.evidence_dir", "evidence"))
        self.retry_limit = int(cfg.get("uploader.retry_limit", 5))
        self.transport = transport or build_transport(cfg)
        self._fail_count: dict[str, int] = {}

    def pending(self) -> list[Path]:
        """待上传的证据包，按 ts_utc_ms 由旧到新——恢复后先补最早的。"""
        out = []
        if not self.root.exists():
            return out
        for mf in self.root.glob("*/*/manifest.json"):
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not all(f.get("uploaded") for f in m.get("files", [])):
                out.append((int(m.get("ts_utc_ms", 0)), mf))
        return [p for _, p in sorted(out)]

    def upload_one(self, manifest_path: Path) -> UploadResult:
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return UploadResult(False, [], [], str(e))
        d = manifest_path.parent

        # 先传元数据：断网恢复后即使文件没传完，云端也已经知道结论
        if not self._retry(lambda: self.transport.send_manifest(m),
                           key=str(manifest_path)):
            return UploadResult(False, [], [f["path"] for f in m.get("files", [])],
                                "元数据上传失败")

        ok, bad = [], []
        for f in m.get("files", []):
            if f.get("uploaded"):
                ok.append(f["path"])
                continue
            p = d / f["path"]
            if not p.exists():
                bad.append(f["path"])
                continue
            key = "%s:%s" % (manifest_path, f["path"])
            if self._retry(lambda p=p, f=f: self.transport.put_file(
                    m["run_id"], m["event_id"], p, f["sha256"]), key=key):
                f["uploaded"] = True
                ok.append(f["path"])
            else:
                f["upload_failed"] = True      # 标记 UPLOAD_FAILED 并保留本地
                bad.append(f["path"])
        manifest_path.write_text(json.dumps(m, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        return UploadResult(not bad, ok, bad)

    def drain(self, limit: int = 8) -> list[UploadResult]:
        return [self.upload_one(p) for p in self.pending()[:limit]]

    def _retry(self, fn, *, key: str) -> bool:
        for attempt in range(self.retry_limit):
            try:
                if fn():
                    self._fail_count.pop(key, None)
                    return True
            except Exception:            # noqa: BLE001
                pass
            time.sleep(min(2.0, 0.2 * (2 ** attempt)))
        self._fail_count[key] = self._fail_count.get(key, 0) + 1
        return False

    def close(self) -> None:
        self.transport.close()
