#!/usr/bin/env python3
"""公开数据集的获取与整理。

    python -m training.prepare_dataset --list      # 要下什么、放哪
    python -m training.prepare_dataset --check     # 检查完整性
    python -m training.prepare_dataset --to-yolo   # 转成 YOLO 目录结构

**脚本不替你绕过任何授权。**Roboflow 需要免费账号才能拿下载链接，`--list`
会把地址与放置路径打印出来，手工下载后再跑 `--to-yolo`。

类别口径按差异清单 A2 的决议：首版三类是压力表、指示灯、开关分合位。
渗漏油没有公开标注数据（方案书 §6.2.1），不在首版。
"""
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "datasets"

#: 首版三类。与 configs/system.yaml 的 mission.first_release_classes 保持一致。
CLASSES = ["PRESSURE_GAUGE", "INDICATOR_LIGHT", "SWITCH_HANDLE"]

#: 公开数据集原始类别 → 本项目类别。原始标注把指示灯按颜色分成四类、
#: 把开关按状态分成两类，而检测阶段只需要知道"这是个指示灯"，
#: 具体是红是绿由 L2 读数环节解算——**分类粒度要与处理链路对齐**，
#: 让检测器去分红绿会把两件事搅在一起，读数算法反而没得做。
LABEL_MAP = {
    "A": "PRESSURE_GAUGE", "V": "PRESSURE_GAUGE", "kV": "PRESSURE_GAUGE",
    "biao": "PRESSURE_GAUGE", "meter": "PRESSURE_GAUGE", "gauge": "PRESSURE_GAUGE",
    "red": "INDICATOR_LIGHT", "green": "INDICATOR_LIGHT",
    "yellow": "INDICATOR_LIGHT", "blue": "INDICATOR_LIGHT",
    "light": "INDICATOR_LIGHT", "indicator": "INDICATOR_LIGHT",
    "connected": "SWITCH_HANDLE", "disconnected": "SWITCH_HANDLE",
    "switch": "SWITCH_HANDLE", "handle": "SWITCH_HANDLE",
}


@dataclass
class Source:
    key: str
    name: str
    url: str
    license: str
    size: str
    note: str
    target: Path


SOURCES = [
    Source("distribution_room", "Roboflow distribution_room",
           "https://universe.roboflow.com/  搜索 distribution_room，导出为 YOLOv8 格式",
           "CC BY 4.0", "2773 张，检测框标注",
           "三类状态量同源，场景/光照/视角一致，不需要做域适配——"
           "这是选它的主要理由", DATA / "distribution_room"),
    Source("paddlex_meter", "PaddleX 工业表计读数数据集",
           "https://paddlex.bj.bcebos.com/datasets/meter_det.tar.gz",
           "百度官方公开", "检测 783 张 / 分割 414 张",
           "指针与刻度的像素级分割标注，公开数据里只有这一份。"
           "本项目读数走几何解算不依赖它，主要用来做读数精度的交叉验证",
           DATA / "paddlex_meter"),
]


def cmd_list() -> int:
    print("需要的公开数据集：\n")
    for s in SOURCES:
        print("  %s" % s.name)
        print("    地址   %s" % s.url)
        print("    许可   %s" % s.license)
        print("    规模   %s" % s.size)
        print("    放到   %s/" % s.target)
        print("    说明   %s\n" % s.note)
    print("下载完成后跑：python -m training.prepare_dataset --check")
    return 0


def cmd_check() -> int:
    ok = True
    for s in SOURCES:
        n_img = len(list(s.target.rglob("*.jpg"))) + len(list(s.target.rglob("*.png")))
        n_lbl = len(list(s.target.rglob("*.txt"))) + len(list(s.target.rglob("*.json")))
        status = "OK" if n_img > 0 else "缺失"
        ok &= n_img > 0
        print("  %-24s %-6s 图 %5d 张，标注 %5d 份  %s"
              % (s.key, status, n_img, n_lbl, s.target))
    if not ok:
        print("\n还有数据没下载，跑 --list 看地址")
    return 0 if ok else 1


def cmd_to_yolo(out: Path) -> int:
    """把原始标注统一到本项目的三类，输出标准 YOLO 目录。"""
    out.mkdir(parents=True, exist_ok=True)
    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    src = SOURCES[0].target
    if not src.exists():
        print("找不到 %s，先跑 --list 按说明下载" % src)
        return 1

    # 原始类别名 → 索引。Roboflow 导出的 data.yaml 里有 names 列表
    names_file = next(src.rglob("data.yaml"), None)
    raw_names: list[str] = []
    if names_file:
        import yaml
        raw_names = list(yaml.safe_load(names_file.read_text(encoding="utf-8")
                                        ).get("names", []))
    if not raw_names:
        print("没找到 data.yaml 里的类别列表，无法映射")
        return 1

    unmapped = sorted({n for n in raw_names if n not in LABEL_MAP})
    if unmapped:
        print("以下原始类别没有映射规则，将被丢弃：%s" % ", ".join(unmapped))
        print("若它们其实属于首版三类，请在 LABEL_MAP 里补上再跑一次")

    n_copy = n_box = 0
    for split in ("train", "valid", "val", "test"):
        img_dir = src / split / "images"
        lbl_dir = src / split / "labels"
        if not img_dir.exists():
            continue
        dst_split = "val" if split in ("valid", "val", "test") else "train"
        for img in sorted(img_dir.iterdir()):
            if img.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            lbl = lbl_dir / (img.stem + ".txt")
            lines_out = []
            if lbl.exists():
                for line in lbl.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) < 5:
                        continue
                    raw = raw_names[int(parts[0])] if int(parts[0]) < len(raw_names) else ""
                    mapped = LABEL_MAP.get(raw)
                    if mapped is None:
                        continue
                    lines_out.append(" ".join([str(CLASSES.index(mapped))] + parts[1:]))
                    n_box += 1
            shutil.copy2(img, out / "images" / dst_split / img.name)
            (out / "labels" / dst_split / (img.stem + ".txt")).write_text(
                "\n".join(lines_out), encoding="utf-8")
            n_copy += 1

    (out / "data.yaml").write_text(
        "path: %s\ntrain: images/train\nval: images/val\nnc: %d\nnames: %s\n"
        % (out.resolve(), len(CLASSES), json.dumps(CLASSES, ensure_ascii=False)),
        encoding="utf-8")
    print("已整理 %d 张图、%d 个框 → %s" % (n_copy, n_box, out))
    print("类别：%s" % ", ".join(CLASSES))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="公开数据集获取与整理")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--to-yolo", action="store_true")
    ap.add_argument("--out", default=str(DATA / "yolo"))
    a = ap.parse_args()
    if a.list:
        return cmd_list()
    if a.check:
        return cmd_check()
    if a.to_yolo:
        return cmd_to_yolo(Path(a.out))
    return cmd_list()


if __name__ == "__main__":
    raise SystemExit(main())
