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
from collections import Counter
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
           "https://bj.bcebos.com/paddlex/examples/meter_reader/datasets/meter_seg.tar.gz",
           "百度官方公开", "分割 374 训练 / 40 验证；检测另见 meter_det.tar.gz",
           "指针与刻度的像素级分割标注，公开数据里只有这一份。"
           "本项目读数走几何解算不依赖它，主要用来做读数精度的交叉验证。"
           "直链 wget 即可，不需登录",
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


#: Roboflow 导出的文件名形如 ``<原名>_jpg.rf.<32位hash>.jpg``：同一张原图经
#: 旋转/裁剪/调色增广出的多张副本，共享 ``.rf.`` 左边那一截，只有 hash 不同。
_RF_SPLIT = ".rf."


def source_key(filename: str) -> str:
    """从文件名反推它来自哪张原图。

    **这是查 train/val 泄漏的关键一步。**Roboflow 的增广副本文件名各不相同，
    光比文件名一个重复都查不出来；但它们的 ``.rf.`` 左边那一截是同一个，
    按那一截归组，跨 split 出现的组就是泄漏。

    不含 ``.rf.`` 的名字（非 Roboflow 导出）退回用去扩展名的全名——
    这时只有完全同名才算重复，不会误报。
    """
    stem = Path(filename).stem
    if _RF_SPLIT in filename:
        return filename.split(_RF_SPLIT, 1)[0]
    return stem


def cmd_check_leak(root: Path) -> int:
    """查 YOLO 目录里 train 与 val 有没有来自同一张原图的增广副本。

    **为什么要单独查这个。**增广副本跨了 split，验证集里就有训练集的近邻，
    mAP 会虚高而且不报错——它看起来只是"模型训得好"。实测巡航级 epoch 1
    就有 mAP50 0.976，这个数必须先排除泄漏才敢往报告里写。

    转换脚本本身不制造泄漏（``cmd_to_yolo`` 沿用 Roboflow 自己的划分），
    所以查的是**上游数据集**带不带这个毛病。
    """
    groups: dict[str, dict[str, list[str]]] = {}
    counts = {}
    for split in ("train", "val"):
        d = root / "images" / split
        if not d.exists():
            print("找不到 %s，先跑 --to-yolo" % d)
            return 1
        names = [f.name for f in sorted(d.iterdir())
                 if f.suffix.lower() in _IMG_EXT]
        counts[split] = len(names)
        for n in names:
            groups.setdefault(source_key(n), {}).setdefault(split, []).append(n)

    shared = {k: v for k, v in groups.items() if len(v) == 2}
    print("  train %d 张 / val %d 张，归并成 %d 张原图"
          % (counts["train"], counts["val"], len(groups)))

    if not shared:
        print("  \033[32mPASS\033[0m  没有原图同时出现在 train 和 val 里")
        return 0

    n_val_leaked = sum(len(v["val"]) for v in shared.values())
    print("  \033[31mFAIL\033[0m  %d 张原图同时出现在两个 split 里，"
          "牵连 val 的 %d/%d 张（%.1f %%）"
          % (len(shared), n_val_leaked, counts["val"],
             100.0 * n_val_leaked / max(1, counts["val"])))
    for k, v in list(sorted(shared.items()))[:5]:
        print("    %s\n      train: %s\n      val:   %s"
              % (k, ", ".join(v["train"][:3]), ", ".join(v["val"][:3])))
    if len(shared) > 5:
        print("    …… 另有 %d 组" % (len(shared) - 5))
    print("\n  验证集里有训练集的增广副本，mAP 会虚高。报告里要么按原图重新"
          "划分后重训，要么如实写明这个局限。")
    return 1


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



# ================================================================== 分割标注
#
# **公开数据里只有 PaddleX 那一份给了像素级的指针标注**，所以分割那一路应当
# 以它为主、合成掩膜做增广，而不是反过来。第一版只写了合成那条路，等于把
# 唯一一份真实像素标注晾在外面——这正是"查了开源数据集却没接进来"。
#
# 两边的类别对不齐，这是接的时候唯一要动脑子的地方：
#
#     本项目   background(0) · face(1) · needle(2) · ticks(3)
#     PaddleX  background(0) · pointer(1) · scale(2)
#
# PaddleX **没有盘面这一类**。它的图是表盘的紧裁剪，所以那些"背景"像素其实
# 大部分就是盘面——但边角又确实是真背景，分不开。硬映射成任何一类都是在教
# 模型一件错事，所以默认映射成 255（忽略）：这些像素不进损失函数。
#
# 这样分工是清楚的：**针与刻度的区分从真实数据学**（这正是合成数据训出来
# 最弱的一环，实测针的 IoU 只有 0.182），**盘面与背景的区分从合成数据学**
# （合成数据在这一维上标得毫无争议）。
IGNORE = 255
_PADDLEX_MAP = {0: IGNORE, 1: 2, 2: 3}          # 见上：0 默认忽略
_BG_CHOICES = {"ignore": IGNORE, "face": 1, "background": 0}
_IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp")
#: 标注可能放在这些同级目录里。PaddleX 各版本的目录名不完全一致，而我这边
#: 的网络出口下不到原始包，没法钉死一种——所以按常见约定逐个试，并把**实际
#: 找到的是哪一种**打印出来，让人一眼能核对。
_ANN_DIRS = ("annotations", "annotation", "masks", "labels", "gt", "SegmentationClass")


def _find_pairs(root: Path) -> list[tuple[Path, Path]]:
    """在 PaddleX 解出来的目录里配对 (图, 标注)。

    不写死目录结构：先收集所有图，再按 stem 去几个常见的标注目录里找同名
    文件。找不到的如实跳过并计数，**不静默丢弃**——数量对不上时人得知道。
    """
    imgs: list[Path] = []
    for ext in _IMG_EXT:
        imgs += [q for q in root.rglob("*" + ext)
                 if not any(d in q.parts for d in _ANN_DIRS)]
    pairs = []
    for im in sorted(set(imgs)):
        ann = None
        for d in _ANN_DIRS:
            for ext in (".png", ".bmp"):
                # PaddleX 的真实目录是 annotations/<split>/<stem>.png，与
                # images/<split>/<stem>.jpg 同 split 并列。第一个候选覆盖这种
                # 带 train/val 子目录的结构；后三个覆盖标注目录扁平摆放的情况。
                for cand in (root / d / im.parent.name / (im.stem + ext),
                             im.parent.parent / d / (im.stem + ext),
                             im.parent / d / (im.stem + ext),
                             root / d / (im.stem + ext)):
                    if cand.exists():
                        ann = cand
                        break
                if ann:
                    break
            if ann:
                break
        if ann is not None:
            pairs.append((im, ann))
    return pairs


def cmd_from_paddlex(src: Path, out: Path, *, background: str = "ignore",
                     val_frac: float = 0.15, seed: int = 0) -> int:
    """把 PaddleX 分割集转成 train_segmenter.py 直接能吃的目录结构。

    产物与 `gen_synthetic.py` 完全一致（images/ masks/ labels/ 三件套），
    所以两份数据可以直接合在一起训——这正是想要的：真实数据教"针 vs 刻度"，
    合成数据补密度分层与盘面标注。
    """
    import random

    import cv2
    import numpy as np

    if not src.exists():
        print("目录不存在：%s" % src)
        print("先下载：见 --list 里 paddlex_meter 那一条")
        return 2
    bg = _BG_CHOICES.get(str(background).lower())
    if bg is None:
        print("--background 只能是 %s" % "/".join(_BG_CHOICES))
        return 2
    pairs = _find_pairs(src)
    if not pairs:
        print("在 %s 下没找到成对的 图/标注。" % src)
        print("找过这些标注目录名：%s" % "、".join(_ANN_DIRS))
        print("如果 PaddleX 的目录名不在其中，把标注目录改名成 annotations/ 再跑。")
        return 2

    for d in ("images/train", "images/val", "masks/train", "masks/val",
              "labels/train", "labels/val", "check"):
        (out / d).mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    seen: Counter = Counter()
    n_ok = n_bad = 0
    for im_p, ann_p in pairs:
        img = cv2.imread(str(im_p))
        ann = cv2.imread(str(ann_p), cv2.IMREAD_UNCHANGED)
        if img is None or ann is None:
            n_bad += 1
            continue
        if ann.ndim == 3:
            ann = ann[:, :, 0]
        if ann.shape[:2] != img.shape[:2]:
            n_bad += 1
            continue
        vals = set(int(v) for v in np.unique(ann))
        seen.update(vals)
        if not vals <= set(_PADDLEX_MAP):
            # 不是 {0,1,2} 的索引图（可能是调色板或 0/128/255 的可视化图），
            # 硬转会得到一份看起来正常、其实类别全错的标注。宁可跳过并报数。
            n_bad += 1
            continue
        m = np.full(ann.shape, bg, np.uint8)
        m[ann == 1] = _PADDLEX_MAP[1]
        m[ann == 2] = _PADDLEX_MAP[2]
        if bg is IGNORE:
            m[ann == 0] = IGNORE
        split = "val" if rng.random() < val_frac else "train"
        stem = "paddlex_" + im_p.stem
        cv2.imwrite(str(out / "images" / split / (stem + ".jpg")), img)
        cv2.imwrite(str(out / "masks" / split / (stem + ".png")), m)
        # PaddleX 的图本来就是单块表的紧裁剪，所以检测框就是整张图。
        # crops_of() 靠它切 ROI，缺了这个文件整帧都会被跳过。
        (out / "labels" / split / (stem + ".txt")).write_text(
            "0 0.5 0.5 1.0 1.0\n", encoding="utf-8")
        if n_ok < 8:
            _seg_check(out / "check" / (stem + ".jpg"), img, m)
        n_ok += 1

    (out / "README.txt").write_text(
        "由 PaddleX 分割集转换而来：%s\n"
        "类别 0=background 1=face 2=needle 3=ticks 255=ignore\n"
        "PaddleX 无盘面标注，其 background 映射为 %s\n"
        % (src.resolve(), background), encoding="utf-8")
    print("转换 %d 张（跳过 %d 张）→ %s" % (n_ok, n_bad, out))
    print("原始标注里出现过的取值：%s" % dict(sorted(seen.items())))
    if n_bad:
        print("跳过的多半是调色板 PNG 或尺寸对不上的。取值不是 {0,1,2} 时"
              "一律不转——硬转会得到一份看起来正常、类别全错的标注。")
    print("**务必人眼看几张 %s/check/**：掩膜错位在任何数字上都看不出来，"
          "只有画出来才看得见。" % out)
    print("接着训：python -m training.train_segmenter --data %s" % out)
    return 0 if n_ok else 2


def _seg_check(path: Path, img, m) -> None:
    """把标注画回图上。和 gen_synthetic.draw_check 同一个用意与同一套配色。"""
    import cv2
    import numpy as np
    color = img.copy()
    color[m == 1] = (90, 140, 60)
    color[m == 2] = (60, 60, 235)
    color[m == 3] = (200, 160, 60)
    out = cv2.addWeighted(img, 0.45, color, 0.55, 0)
    out[m == IGNORE] = (out[m == IGNORE] * 0.55).astype(np.uint8)   # 忽略区压暗
    cv2.imwrite(str(path), out)


def main() -> int:
    ap = argparse.ArgumentParser(description="公开数据集获取与整理")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--to-yolo", action="store_true")
    ap.add_argument("--check-leak", action="store_true",
                    help="查 --to-yolo 产出的 train/val 有没有同一张原图的增广副本")
    ap.add_argument("--from-paddlex", default=None, metavar="DIR",
                    help="把 PaddleX 分割集转成 train_segmenter 能吃的结构")
    ap.add_argument("--background", default="ignore",
                    choices=sorted(_BG_CHOICES),
                    help="PaddleX 的 background 映射成什么（默认忽略，不进损失）")
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=str(DATA / "yolo"))
    a = ap.parse_args()
    if a.from_paddlex:
        out = Path(a.out if a.out != str(DATA / "yolo") else DATA / "seg_paddlex")
        return cmd_from_paddlex(Path(a.from_paddlex), out,
                                background=a.background, val_frac=a.val_frac,
                                seed=a.seed)
    if a.list:
        return cmd_list()
    if a.check:
        return cmd_check()
    if a.to_yolo:
        return cmd_to_yolo(Path(a.out))
    if a.check_leak:
        return cmd_check_leak(Path(a.out))
    return cmd_list()


if __name__ == "__main__":
    raise SystemExit(main())
