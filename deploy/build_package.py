#!/usr/bin/env python3
"""打 RK3576 部署包：dist/patrol-rk3576-<版本>.tar.gz。在开发机上跑（Windows 或 Linux 都行）。

    python deploy/build_package.py \\
        --yolo-rknn-dir  \\\\wsl.localhost\\Ubuntu\\home\\<you>\\rknn\\yolo\\out \\
        --padim-rknn-dir \\\\wsl.localhost\\Ubuntu\\home\\<you>\\rknn\\out

步骤：
1. 收模型到 models/（.gitignore 挡掉，不进版本库）
   - 检测器：<yolo-rknn-dir>/<模型>_<精度>.rknn → models/<模型>.rknn，精度取
     deliverables/甲-检测/rknn/rknn_report.json 的 default_choice
   - L3：<padim-rknn-dir>/padim_net{2,3}_<精度>.rknn → models/padim_net{2,3}.rknn，
     以及 training/runs/anomaly/padim_cov_stats.npz（没有就先跑 python -m training.export_padim_stats）
2. 暂存：代码、配置、文档、deploy/、models/；把检测器实际精度写进暂存区的 configs/rk3576.yaml
3. 写 SHA256SUMS 与 VERSION，打 tar.gz（.sh 带可执行位，Windows 上打也一样）
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODELS = ("cruise_ft", "verify_ft")
STAGE_OF = {"cruise_ft": "cruise", "verify_ft": "verify"}
INCLUDE = ["patrol", "cloud", "configs", "docs", "deploy", "models", "README.md", "LICENSE"]
EXCLUDE_DIRS = {"__pycache__", "storage", ".pytest_cache"}
EXCLUDE_FILES = {"patrol.db", "patrol.db-wal", "patrol.db-shm"}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def detector_choice(repo: Path) -> dict[str, str]:
    rep = repo / "deliverables" / "甲-检测" / "rknn" / "rknn_report.json"
    if not rep.exists():
        raise SystemExit("缺 %s：先在 WSL 里跑 training/export_rknn_yolo.py" % rep)
    choice = json.loads(rep.read_text(encoding="utf-8")).get("default_choice", {})
    return {m: choice.get(m, "fp16") for m in MODELS}


def collect_models(repo: Path, yolo_dir: Path, padim_dir: Path, padim_dtype: str, choice: dict) -> Path:
    dst = repo / "models"
    dst.mkdir(exist_ok=True)
    plan = [(yolo_dir / ("%s_%s.rknn" % (m, choice[m])), dst / ("%s.rknn" % m)) for m in MODELS]
    plan += [(padim_dir / ("padim_net%d_%s.rknn" % (k, padim_dtype)), dst / ("padim_net%d.rknn" % k)) for k in (2, 3)]
    plan.append((repo / "training" / "runs" / "anomaly" / "padim_cov_stats.npz", dst / "padim_cov_stats.npz"))
    missing = [str(s) for s, _ in plan if not s.exists()]
    if missing:
        raise SystemExit("缺模型文件：\n  " + "\n  ".join(missing))
    for s, d in plan:
        shutil.copyfile(s, d)
        print("模型 %-24s ← %s" % (d.name, s))
    return dst


def _patch_quant(cfg_path: Path, choice: dict) -> None:
    """在部署配置的 perception 段下写入检测器实际精度（INT8 / FP16）。"""
    lines = cfg_path.read_text(encoding="utf-8").splitlines()
    out = []
    for ln in lines:
        out.append(ln)
        if ln.strip() == "detector: rknn":
            indent = ln[: len(ln) - len(ln.lstrip())]
            out.append("%srknn:" % indent)
            for m in MODELS:
                out.append("%s  quant_%s: %s" % (indent, STAGE_OF[m], choice[m].upper()))
    cfg_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def stage(repo: Path, out_dir: Path, version: str, choice: dict) -> Path:
    root = out_dir / ("patrol-rk3576-%s" % version)
    if root.exists():
        shutil.rmtree(root)
    for name in INCLUDE:
        src = repo / name
        if not src.exists():
            continue
        if src.is_file():
            root.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, root / name)
            continue
        for p in src.rglob("*"):
            rel = p.relative_to(repo)
            if p.is_dir() or any(part in EXCLUDE_DIRS for part in rel.parts) or p.name in EXCLUDE_FILES \
                    or p.suffix == ".pyc":
                continue
            t = root / rel
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, t)
    _patch_quant(root / "configs" / "rk3576.yaml", choice)
    try:
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    (root / "VERSION").write_text("version: %s\ncommit: %s\nbuilt: %s\ndetector: %s\n" % (
        version, commit, time.strftime("%Y-%m-%d %H:%M"), json.dumps(choice)), encoding="utf-8")
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (root / "SHA256SUMS").write_text(
        "".join("%s  %s\n" % (_sha256(p), p.relative_to(root).as_posix()) for p in files), encoding="utf-8")
    return root


def make_tar(root: Path) -> Path:
    tar_path = root.parent / (root.name + ".tar.gz")
    with tarfile.open(tar_path, "w:gz") as tar:
        for p in sorted(root.rglob("*")):
            arc = (Path(root.name) / p.relative_to(root)).as_posix()
            info = tar.gettarinfo(str(p), arcname=arc)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            if p.is_dir():
                info.mode = 0o755
                tar.addfile(info)
            else:
                info.mode = 0o755 if p.suffix == ".sh" else 0o644
                with open(p, "rb") as f:
                    tar.addfile(info, f)
    return tar_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="打 RK3576 部署包")
    ap.add_argument("--yolo-rknn-dir", required=True, help="export_rknn_yolo.py 的输出目录（…/rknn/yolo/out）")
    ap.add_argument("--padim-rknn-dir", required=True, help="PaDiM 特征网络 RKNN 所在目录（…/rknn/out）")
    ap.add_argument("--padim-dtype", default="int8", choices=["int8", "fp"])
    ap.add_argument("--version", default=time.strftime("%Y%m%d"))
    ap.add_argument("--out-dir", default=str(REPO / "dist"))
    a = ap.parse_args(argv)
    choice = detector_choice(REPO)
    collect_models(REPO, Path(a.yolo_rknn_dir), Path(a.padim_rknn_dir), a.padim_dtype, choice)
    root = stage(REPO, Path(a.out_dir), a.version, choice)
    tar_path = make_tar(root)
    print("\n部署包：%s（%.1f MB）\n检测器精度：%s；L3 特征网络：%s" % (
        tar_path, tar_path.stat().st_size / 1e6, choice, a.padim_dtype))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
