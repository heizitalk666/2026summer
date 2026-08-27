"""证据包上传队列：断点续传、退避、不阻塞主循环。

这个模块此前覆盖率 38 %——重传、退避、断网这几条路径一次都没跑过，而它们
恰恰只在出事的时候才执行。三条纪律各有一条用例钉着：

1. **不往 manifest 里塞本地状态**（Schema 是 additionalProperties: false）
2. **不在主循环里睡**（断网时 uploader 还要继续在本地打包）
3. **失败多的排到队尾**（一个坏包不能饿死整条队列）
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from patrol.common import messages as M
from patrol.common.config import Config
from patrol.uploader.transport import UploadQueue


class _Transport:
    """可控的假传输。manifest / 文件各自可以设成失败。"""

    def __init__(self, *, manifest_ok=True, file_ok=True, raises=False):
        self.manifest_ok = manifest_ok
        self.file_ok = file_ok
        self.raises = raises
        self.manifests = 0
        self.files: list[str] = []

    def send_manifest(self, m):
        self.manifests += 1
        if self.raises:
            raise ConnectionError("云端不可达")
        return self.manifest_ok

    def put_file(self, run_id, event_id, path, sha256):
        if self.raises:
            raise ConnectionError("云端不可达")
        if self.file_ok:
            self.files.append(path.name)
        return self.file_ok

    def close(self):
        pass


def make_package(root: Path, event_id: str, *, ts=1_700_000_000_000,
                 n_files=2) -> Path:
    d = root / "20260901-093012-a7f3" / event_id
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(n_files):
        name = "cruise.jpg" if i == 0 else "meta.jsonl"
        (d / name).write_bytes(b"x" * (16 + i))
        files.append({"path": name,
                      "role": "CRUISE_ANNOTATED" if i == 0 else "META_LOG",
                      "bytes": 16 + i, "sha256": "0" * 64, "uploaded": False})
    snap = {"confidence": 0.45, "pixel_density_px": 49.9, "zoom": 1.0,
            "est_distance_m": 5.06, "defect_class": "PRESSURE_GAUGE",
            "l2_reading": None}
    after = dict(snap, confidence=0.91, pixel_density_px=119.8, zoom=2.4)
    manifest = M.build_evidence_package(
        run_id="20260901-093012-a7f3", event_id=event_id, waypoint_id="WP-07",
        ts_utc_ms=ts,
        verdict={"result": "READING_OK", "defect_class": "PRESSURE_GAUGE",
                 "severity": "INFO", "needs_human_review": False, "confidence": 0.91},
        before=snap, after=after,
        gain={"delta_conf": 0.46, "pixel_density_ratio": 2.4, "verify_success": True},
        timeline=[], files=files, abort=None)
    mf = d / "manifest.json"
    mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return mf


@pytest.fixture()
def queue_factory(tmp_path):
    def build(**tkw):
        t = _Transport(**tkw)
        cfg = Config.load(overrides={"logging": {"dir": str(tmp_path / "logs")},
                                     "uploader": {"evidence_dir": str(tmp_path / "ev")}})
        return UploadQueue(cfg, transport=t), t, tmp_path / "ev"
    return build


# ---------------------------------------------------------------- 正常路径
def test_successful_upload_marks_every_file(queue_factory):
    q, t, root = queue_factory()
    mf = make_package(root, "aaaa1111-0000-4000-8000-000000000001")
    res = q.upload_one(mf)
    assert res.ok and len(res.uploaded) == 2 and not res.failed
    m = json.loads(mf.read_text(encoding="utf-8"))
    assert all(f["uploaded"] for f in m["files"])
    assert q.pending() == [], "全部传完之后不该还在队列里"


def test_already_uploaded_files_are_not_resent(queue_factory):
    q, t, root = queue_factory()
    mf = make_package(root, "aaaa1111-0000-4000-8000-000000000002")
    q.upload_one(mf)
    sent = len(t.files)
    q.upload_one(mf)
    assert len(t.files) == sent, "已确认的文件又被重传了一遍"


def test_metadata_goes_first(queue_factory):
    """先传元数据再传文件：断网恢复后即使文件没传完，云端也已经知道结论。"""
    q, t, root = queue_factory(file_ok=False)
    mf = make_package(root, "aaaa1111-0000-4000-8000-000000000003")
    q.upload_one(mf)
    assert t.manifests == 1 and t.files == []


# ---------------------------------------------------------------- 失败路径
def test_manifest_stays_schema_valid_after_a_failed_upload(queue_factory):
    """**上传失败不能把 manifest 写坏。**

    manifest 是 IF-4 的报文本体，`files[]` 是 additionalProperties: false。
    原来失败时会往里写一个 `upload_failed: true`，写完这份 manifest 就校验
    不过了——云端、validate.py、将来的回放工具都会拒收它。
    """
    q, t, root = queue_factory(file_ok=False)
    mf = make_package(root, "bbbb2222-0000-4000-8000-000000000001")
    q.upload_one(mf)
    m = json.loads(mf.read_text(encoding="utf-8"))
    M.validate(m)                                    # 不抛异常即通过
    extra = set(m["files"][0]) - {"path", "role", "bytes", "sha256", "uploaded"}
    assert not extra, "manifest 里混进了本地状态字段：%s" % extra


def test_local_retry_state_lives_in_a_sidecar(queue_factory):
    q, t, root = queue_factory(file_ok=False)
    mf = make_package(root, "bbbb2222-0000-4000-8000-000000000002")
    q.upload_one(mf)
    side = mf.parent / "upload_state.json"
    assert side.exists(), "本地重传账没有落到 upload_state.json"
    assert json.loads(side.read_text(encoding="utf-8"))["rounds"] >= 1


def test_a_raising_transport_does_not_escape(queue_factory):
    """云端不可达时抛的异常不能冒到 uploader 的主循环上。"""
    q, t, root = queue_factory(raises=True)
    mf = make_package(root, "bbbb2222-0000-4000-8000-000000000003")
    res = q.upload_one(mf)
    assert res.ok is False and res.failed


def test_failed_package_stays_in_the_queue(queue_factory):
    """**未确认的数据永不自动删除**（方案书 §8.3.5）。"""
    q, t, root = queue_factory(file_ok=False)
    mf = make_package(root, "bbbb2222-0000-4000-8000-000000000004")
    q.upload_one(mf)
    assert mf in q.pending()
    assert (mf.parent / "cruise.jpg").exists()


# ---------------------------------------------------------------- 不阻塞
def test_drain_does_not_block_the_event_loop(queue_factory):
    """**断网时 drain 必须立刻返回。**

    原来的重试是"5 次尝试，每次之间指数退避"，一个失败的包最坏阻塞 5 s；
    断网时 uploader 的 step() 会被卡上几分钟，期间不再排空 IF-1/IF-3，也
    不再打包新的证据——而断网正是最需要它继续在本地留证的时候。
    """
    q, t, root = queue_factory(manifest_ok=False)
    for i in range(4):
        make_package(root, "cccc3333-0000-4000-8000-00000000000%d" % i)
    t0 = time.monotonic()
    q.drain(limit=4)
    dt = time.monotonic() - t0
    assert dt < 0.5, "四个失败的包让 drain 阻塞了 %.1f s" % dt


def test_backoff_stops_hammering_a_dead_cloud(queue_factory):
    """连续失败之后进入退避窗口，同一轮里不再反复敲云端。"""
    q, t, root = queue_factory(manifest_ok=False)
    mf = make_package(root, "cccc3333-0000-4000-8000-000000000010")
    q.upload_one(mf)
    first = t.manifests
    for _ in range(5):
        q.upload_one(mf)
    assert t.manifests == first, "退避没生效，仍在每轮重试"


def test_backoff_expires_and_upload_recovers(queue_factory):
    """退避到期后必须重新尝试——网络恢复了不能一直不理。"""
    q, t, root = queue_factory(manifest_ok=False)
    q.MAX_BACKOFF_S = 0.05
    mf = make_package(root, "cccc3333-0000-4000-8000-000000000011")
    q.upload_one(mf)
    t.manifest_ok = True
    time.sleep(0.12)
    res = q.upload_one(mf)
    assert res.ok, "退避到期后没有重新尝试"


# ---------------------------------------------------------------- 排队
def test_a_permanently_failing_package_does_not_starve_the_others(queue_factory):
    """**失败多的排到队尾。**

    一个永远传不上去的包（云端拒收之类）会一直占着队头，把后面所有包饿死。
    它仍然会被重试，只是不再挡路。
    """
    q, t, root = queue_factory(manifest_ok=False)
    bad = make_package(root, "dddd4444-0000-4000-8000-000000000001", ts=1)
    for _ in range(3):
        q.upload_one(bad)
        q._next_try.clear()                          # noqa: SLF001 - 跳过退避
    good = make_package(root, "dddd4444-0000-4000-8000-000000000002", ts=9_000_000)
    order = q.pending()
    assert order[0] == good, "失败三轮的包还占着队头：%s" % [p.parent.name for p in order]
    assert bad in order, "失败的包被丢掉了——未确认的数据永不自动删除"


def test_oldest_first_among_equally_healthy_packages(queue_factory):
    """同样没传过的包，按 ts_utc_ms 由旧到新——恢复后先补最早的。"""
    q, t, root = queue_factory()
    newer = make_package(root, "eeee5555-0000-4000-8000-000000000002", ts=2_000)
    older = make_package(root, "eeee5555-0000-4000-8000-000000000001", ts=1_000)
    assert q.pending() == [older, newer]


def test_missing_file_is_reported_not_crashed(queue_factory):
    """图片被磁盘水位管理删掉了，也要照常返回失败清单而不是抛异常。"""
    q, t, root = queue_factory()
    mf = make_package(root, "eeee5555-0000-4000-8000-000000000003")
    (mf.parent / "cruise.jpg").unlink()
    res = q.upload_one(mf)
    assert "cruise.jpg" in res.failed and res.ok is False


def test_corrupt_manifest_is_skipped_not_fatal(queue_factory):
    q, t, root = queue_factory()
    mf = make_package(root, "eeee5555-0000-4000-8000-000000000004")
    mf.write_text("{ 这不是 json", encoding="utf-8")
    assert q.pending() == []                          # 读不动就跳过
    res = q.upload_one(mf)
    assert res.ok is False and res.error
