"""RK3576 部署配置与部署包。不需要板子，也不需要 RKNN 模型文件。"""
from __future__ import annotations

import importlib.util
import tarfile
from pathlib import Path

from patrol.common.config import CONFIG_DIR, Config

ROOT = Path(__file__).resolve().parents[1]


def _builder():
    spec = importlib.util.spec_from_file_location("build_package", ROOT / "deploy" / "build_package.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rk3576_config_switches_only_drivers_and_backends():
    base = Config.load(CONFIG_DIR / "system.yaml")
    rk = Config.load(CONFIG_DIR / "rk3576.yaml")
    assert rk.get("driver_mode") == "real"
    assert rk.get("perception.detector") == "rknn"
    assert rk.get("perception.l3.model") == "padim_np"
    assert rk.get("perception.l3.backend") == "rknn"
    # 其余照 system.yaml：节拍、阈值、标定表、串口配置都不能被这份覆盖丢掉
    for key in ("perception.fps", "perception.model.cruise.conf_threshold", "perception.l3.threshold",
                "perception.l3.min_density_px", "real.serial.chassis.port", "perception.rknn.weights_cruise"):
        assert rk.get(key) == base.get(key), key
    assert [t["id"] for t in rk.get("scene.targets")] == [t["id"] for t in base.get("scene.targets")]


def test_package_stage_and_tar(tmp_path):
    b = _builder()
    choice = {"cruise_ft": "int8", "verify_ft": "fp16"}
    root = b.stage(ROOT, tmp_path, "test", choice)
    cfg = Config.load(root / "configs" / "rk3576.yaml")
    assert cfg.get("perception.rknn.quant_cruise") == "INT8"
    assert cfg.get("perception.rknn.quant_verify") == "FP16"
    assert cfg.get("perception.rknn.weights_verify") == "models/verify_ft.rknn"
    sums = (root / "SHA256SUMS").read_text(encoding="utf-8")
    assert "deploy/install.sh" in sums and "patrol/perception/detector/exported_yolo.py" in sums
    assert "__pycache__" not in sums and "patrol.db" not in sums
    tar_path = b.make_tar(root)
    with tarfile.open(tar_path) as tar:
        members = {m.name: m for m in tar.getmembers()}
    install = members["patrol-rk3576-test/deploy/install.sh"]
    assert install.mode & 0o111, "install.sh 在包里必须可执行"
    assert not any("__pycache__" in n for n in members)
