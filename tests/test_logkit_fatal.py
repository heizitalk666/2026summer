"""节点异常退出时，logs/<node>.jsonl 里必须留下死因。"""
from __future__ import annotations

import json

import pytest

from patrol.common.config import Config
from patrol.common.logkit import fatal_guard


def _records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_fatal_guard_writes_traceback_and_reraises(tmp_path):
    cfg = Config.load(overrides={"logging": {"dir": str(tmp_path)}})

    def serve_forever():
        raise RuntimeError("相机帧尺寸异常")

    with pytest.raises(RuntimeError):
        with fatal_guard("perception", cfg):
            serve_forever()
    recs = _records(tmp_path / "perception.jsonl")
    last = recs[-1]
    assert last["level"] == "CRITICAL"
    assert last["exc_type"] == "RuntimeError"
    assert "相机帧尺寸异常" in last["exc"]
    assert "serve_forever" in last["traceback"]


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_fatal_guard_ignores_normal_exits(tmp_path, exc):
    cfg = Config.load(overrides={"logging": {"dir": str(tmp_path)}})
    with pytest.raises(exc):
        with fatal_guard("mission", cfg):
            raise exc()
    assert not (tmp_path / "mission.jsonl").exists()
