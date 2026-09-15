"""run_all --config 通过 PATROL_CONFIG 把配置传给子进程，Config.load() 必须读它。"""
from __future__ import annotations

from patrol.common.config import CONFIG_DIR, Config


def test_patrol_config_env_is_honoured(tmp_path, monkeypatch):
    alt = tmp_path / "alt.yaml"
    lines = ["includes:", "  - %s" % (CONFIG_DIR / "system.yaml").as_posix(),
             "perception:", "  detector: onnx", ""]
    alt.write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.setenv("PATROL_CONFIG", str(alt))
    assert Config.load().get("perception.detector") == "onnx"


def test_explicit_path_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PATROL_CONFIG", str(tmp_path / "does_not_exist.yaml"))
    assert Config.load(CONFIG_DIR / "system.yaml").get("driver_mode") in ("stub", "real")


def test_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("PATROL_CONFIG", raising=False)
    default = Config.load(CONFIG_DIR / "system.yaml")
    assert Config.load().get("perception.detector") == default.get("perception.detector")
