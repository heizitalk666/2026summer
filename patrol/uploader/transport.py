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
        self._host = str(m.get("host", "127.0.0.1"))
        self._port = int(m.get("port", 1883))
        self._c = mqtt.Client()
        self._connected = False
        self._c.loop_start()
        self._ensure_connected()

    def _ensure_connected(self) -> bool:
        """**连不上 broker 不能让整个节点起不来。**

        原来 connect() 写在构造函数里且不捕异常：mosquitto 没起来时
        UploaderNode 直接构造失败，边缘端连本地打包都做不了了——而断网恰恰
        是最需要它继续在本地留证的时候。改成惰性重连，失败就这一轮不发，
        证据包留在本地队列里等下一轮。
        """
        if self._connected:
            return True
        try:
            self._c.connect(self._host, self._port, 30)
            self._connected = True
        except OSError:
            self._connected = False
        return self._connected

    def send_manifest(self, manifest: dict) -> bool:
        if not self._ensure_connected():
            return False
        topic = self.topic_tpl.format(site_id=self.site, run_id=manifest["run_id"])
        payload = json.dumps(manifest, ensure_ascii=False)
        if len(payload.encode("utf-8")) > 16384:
            # ICD §6.6：单条元数据 < 16 KB
            return False
        try:
            info = self._c.publish(topic, payload, qos=self.qos)
            info.wait_for_publish(timeout=5.0)
            return info.is_published()
        except (OSError, ValueError, RuntimeError):
            self._connected = False          # 下一轮重连
            return False

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

    三条设计约束，每一条都是踩过之后写下来的：

    **一、不往 manifest 里塞本地状态。**manifest 是 IF-4 的报文本体，Schema
    的 `files[]` 是 `additionalProperties: false`。原来上传失败时会往里写一个
    `upload_failed: true`，写完这份 manifest 就**校验不过**了——云端、
    validate.py、将来的回放工具都会拒收它。重传次数这类本地账记在旁边的
    `upload_state.json` 里，manifest 只保留 Schema 里有的 `uploaded`。

    **二、不在主循环里睡。**原来的重试是"5 次尝试，每次之间指数退避"，一个
    包最坏阻塞 5 s；断网时 uploader 的 step() 会被卡上几分钟，期间不再排空
    IF-1/IF-3，也不再打包新的证据——偏偏断网正是最需要它继续在本地打包的
    时候。改成**每轮只试一次**，退避靠"下次可以试的时间戳"表达，一次
    drain() 立刻返回。

    **三、失败多的排到队尾。**一个永远传不上去的包（比如云端拒收）会一直占着
    队头，把后面所有包饿死。排序键改成 (失败轮数, 时间戳)，它自然沉底，
    但**仍然会被重试**，不删也不放弃。
    """

    #: 退避上限。断网时不必每 3 s 敲一次云端，但也不能久到网络恢复了半天没反应。
    MAX_BACKOFF_S = 30.0

    def __init__(self, cfg, transport: ITransport | None = None):
        self.root = Path(cfg.get("uploader.evidence_dir", "evidence"))
        self.retry_limit = int(cfg.get("uploader.retry_limit", 5))
        self.transport = transport or build_transport(cfg)
        self._fail: dict[str, int] = {}
        self._next_try: dict[str, float] = {}

    # ---- 本地上传状态（不进 manifest） -----------------------------
    @staticmethod
    def _state_path(manifest_path: Path) -> Path:
        return manifest_path.parent / "upload_state.json"

    def _load_state(self, manifest_path: Path) -> dict:
        try:
            return json.loads(self._state_path(manifest_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"rounds": 0, "failed": []}

    def _save_state(self, manifest_path: Path, state: dict) -> None:
        try:
            self._state_path(manifest_path).write_text(
                json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    # ---- 队列 -------------------------------------------------------
    def pending(self) -> list[Path]:
        """待上传的证据包。**失败多的排到队尾**，避免一个坏包饿死整条队列。"""
        out = []
        if not self.root.exists():
            return out
        for mf in self.root.glob("*/*/manifest.json"):
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if all(f.get("uploaded") for f in m.get("files", [])):
                continue
            rounds = int(self._load_state(mf).get("rounds", 0))
            out.append((rounds, int(m.get("ts_utc_ms", 0)), mf))
        return [p for _, _, p in sorted(out)]

    def upload_one(self, manifest_path: Path) -> UploadResult:
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return UploadResult(False, [], [], str(e))
        d = manifest_path.parent
        state = self._load_state(manifest_path)

        # 先传元数据：断网恢复后即使文件没传完，云端也已经知道结论
        if not self._attempt(lambda: self.transport.send_manifest(m),
                             key=str(manifest_path)):
            state["rounds"] = int(state.get("rounds", 0)) + 1
            self._save_state(manifest_path, state)
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
            if self._attempt(lambda p=p, f=f: self.transport.put_file(
                    m["run_id"], m["event_id"], p, f["sha256"]), key=key):
                f["uploaded"] = True          # 这个字段在 Schema 里，可以写
                ok.append(f["path"])
            else:
                bad.append(f["path"])
        # **只写 Schema 允许的字段。**本地重传账记在 upload_state.json。
        manifest_path.write_text(json.dumps(m, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        state["failed"] = bad
        if bad:
            state["rounds"] = int(state.get("rounds", 0)) + 1
        self._save_state(manifest_path, state)
        return UploadResult(not bad, ok, bad)

    def drain(self, limit: int = 8) -> list[UploadResult]:
        return [self.upload_one(p) for p in self.pending()[:limit]]

    def _attempt(self, fn, *, key: str) -> bool:
        """试一次，**不阻塞**。失败则按指数退避安排下次可试的时间。

        原来这里是 `for attempt in range(retry_limit): ... time.sleep(...)`，
        一个失败的包最坏要睡 5 s，把 uploader 的主循环整个卡住。退避改成
        用时间戳表达之后，一次调用立刻返回，退避语义不变。
        """
        now = time.monotonic()
        if now < self._next_try.get(key, 0.0):
            return False                      # 还在退避窗口里，这一轮直接跳过
        try:
            if fn():
                self._next_try.pop(key, None)
                self._fail.pop(key, None)
                return True
        except Exception:            # noqa: BLE001
            pass
        n = self._fail.get(key, 0) + 1
        self._fail[key] = n
        self._next_try[key] = now + min(self.MAX_BACKOFF_S, 0.5 * (2 ** min(n, 6)))
        return False

    def failures(self, key: str) -> int:
        """某个键连续失败了几次。retry_limit 之后仍然重试，只是排到队尾。"""
        return self._fail.get(key, 0)

    def close(self) -> None:
        self.transport.close()
