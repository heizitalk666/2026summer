"""磁盘保留策略。ICD §6.5 / 方案书 §7.3.3 与 §8.3.5。

**这是全仓库唯一会主动删文件的地方，而它此前一次都没被执行过。**删除逻辑
判错方向的代价是不可逆的：把没上传成功的证据包删掉，等于把最该留证的那份
数据毁了——它恰恰没能送到任何别的地方。

两条设计原则在这里打架，用例要钉住主次：

- 磁盘满不能导致巡检停止（§7.3.3）
- **未确认的数据永不自动删除**（§8.3.5）
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from patrol.common.config import Config
from patrol.uploader.packer import EvidencePacker


def make_pack(root: Path, name: str, *, uploaded: bool, age_days: float = 0.0,
              size_kb: int = 8, manifest: bool = True) -> Path:
    d = root / "20260901-093012-a7f3" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "cruise.jpg").write_bytes(b"x" * (size_kb * 1024))
    if manifest:
        (d / "manifest.json").write_text(json.dumps({
            "files": [{"path": "cruise.jpg", "role": "CRUISE_ANNOTATED",
                       "bytes": size_kb * 1024, "sha256": "0" * 64,
                       "uploaded": uploaded}]}), encoding="utf-8")
    old = time.time() - age_days * 86400
    for p in list(d.rglob("*")) + [d]:
        import os
        os.utime(p, (old, old))
    return d


def packer(tmp_path, *, max_days=7.0, max_gb=20.0):
    cfg = Config.load(overrides={
        "logging": {"dir": str(tmp_path / "logs")},
        "uploader": {"evidence_dir": str(tmp_path / "ev"),
                     "retention": {"max_days": max_days, "max_gb": max_gb}}})
    return EvidencePacker(cfg)


# ---------------------------------------------------------------- 不删的
def test_unconfirmed_data_is_never_deleted_by_age(tmp_path):
    """**过期也不删没传上去的。**

    原来按时间到期就删，不看 uploaded——一个连传七天没成功的证据包会被
    自己删掉，而它恰恰是最该留证的那种：没能送到任何别的地方。
    """
    pk = packer(tmp_path, max_days=7.0)
    old_unsent = make_pack(pk.root, "aaaa", uploaded=False, age_days=30)
    r = pk.enforce_retention()
    assert old_unsent.exists(), "过期但未确认上传的证据包被删了"
    assert r["removed"] == 0 and r["kept_unconfirmed"] == 1


def test_unconfirmed_data_is_never_deleted_by_quota(tmp_path):
    """配额爆了也不删未确认的，改成如实报出来交给人处理。"""
    pk = packer(tmp_path, max_gb=1e-6)          # 配额小到必然超
    kept = make_pack(pk.root, "bbbb", uploaded=False, size_kb=64)
    r = pk.enforce_retention()
    assert kept.exists()
    assert r["over_quota"] is True, "只剩未确认的还超配额时要报出来"
    assert r["kept_unconfirmed"] == 1


def test_a_package_without_manifest_counts_as_unconfirmed(tmp_path):
    """没有 manifest 就当未确认——判错方向的代价不对称。

    错判成已确认会把没送出去的证据删掉；错判成未确认只是多占一点盘。
    """
    pk = packer(tmp_path, max_days=1.0)
    d = make_pack(pk.root, "cccc", uploaded=True, age_days=30, manifest=False)
    pk.enforce_retention()
    assert d.exists()


def test_a_corrupt_manifest_counts_as_unconfirmed(tmp_path):
    pk = packer(tmp_path, max_days=1.0)
    d = make_pack(pk.root, "dddd", uploaded=True, age_days=30)
    (d / "manifest.json").write_text("{ 坏 json", encoding="utf-8")
    pk.enforce_retention()
    assert d.exists()


def test_partially_uploaded_package_is_kept(tmp_path):
    """只要还有一个文件没确认，整包都不能删。"""
    pk = packer(tmp_path, max_days=1.0)
    d = make_pack(pk.root, "eeee", uploaded=True, age_days=30)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["files"].append({"path": "meta.jsonl", "role": "META_LOG", "bytes": 1,
                       "sha256": "0" * 64, "uploaded": False})
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    pk.enforce_retention()
    assert d.exists()


def test_fresh_confirmed_package_within_quota_is_kept(tmp_path):
    pk = packer(tmp_path, max_days=7.0, max_gb=20.0)
    d = make_pack(pk.root, "ffff", uploaded=True, age_days=0.1)
    assert pk.enforce_retention()["removed"] == 0
    assert d.exists()


# ---------------------------------------------------------------- 该删的
def test_expired_confirmed_package_is_removed(tmp_path):
    pk = packer(tmp_path, max_days=7.0)
    d = make_pack(pk.root, "1111", uploaded=True, age_days=30)
    r = pk.enforce_retention()
    assert not d.exists()
    assert r["removed"] == 1 and r["freed_mb"] > 0


def test_age_cleanup_still_works_behind_a_fresh_package(tmp_path):
    """**这条钉的是一个真 bug。**

    排序是"已上传的优先、其次按时间从旧到新"，而循环遇到第一个"既不过期也
    不超配额"的包就 break。于是只要队头有一个新的、已上传的包，后面那些真正
    过期的就永远轮不到——按时间清理静默失效，而且完全没有征兆。
    """
    pk = packer(tmp_path, max_days=7.0)
    fresh = make_pack(pk.root, "2222", uploaded=True, age_days=0.1)
    stale_a = make_pack(pk.root, "3333", uploaded=True, age_days=30)
    stale_b = make_pack(pk.root, "4444", uploaded=True, age_days=40)
    r = pk.enforce_retention()
    assert fresh.exists(), "没过期的被删了"
    assert not stale_a.exists() and not stale_b.exists(), \
        "过期的包排在新包后面，就再也删不掉了"
    assert r["removed"] == 2


def test_quota_cleanup_removes_oldest_confirmed_first(tmp_path):
    """超配额时按时间从旧到新删已确认的，删够为止。"""
    pk = packer(tmp_path, max_days=3650.0, max_gb=100.0 / (1 << 30))   # 100 B 配额
    old = make_pack(pk.root, "5555", uploaded=True, age_days=5, size_kb=8)
    new = make_pack(pk.root, "6666", uploaded=True, age_days=1, size_kb=8)
    r = pk.enforce_retention()
    assert not old.exists(), "超配额时应当先删最旧的"
    assert r["removed"] >= 1


def test_removed_count_is_not_inflated(tmp_path):
    """统计数字不能虚报：删失败就不计数。"""
    pk = packer(tmp_path, max_days=7.0)
    make_pack(pk.root, "7777", uploaded=True, age_days=30)
    r = pk.enforce_retention()
    remaining = [d for d in (pk.root / "20260901-093012-a7f3").iterdir()]
    assert r["removed"] == 1 and remaining == []


def test_empty_root_is_a_noop(tmp_path):
    pk = packer(tmp_path)
    r = pk.enforce_retention()
    assert r == {"removed": 0, "freed_mb": 0.0, "kept_unconfirmed": 0,
                 "over_quota": False}


def test_stray_files_in_the_evidence_root_do_not_crash_it(tmp_path):
    pk = packer(tmp_path, max_days=7.0)
    pk.root.mkdir(parents=True, exist_ok=True)
    (pk.root / "随手放的.txt").write_text("x", encoding="utf-8")
    (pk.root / "20260901-093012-a7f3").mkdir(parents=True, exist_ok=True)
    (pk.root / "20260901-093012-a7f3" / "note.txt").write_text("x", encoding="utf-8")
    assert pk.enforce_retention()["removed"] == 0
