"""公开数据集的接入。

**这条通路的价值在于：公开数据里只有 PaddleX 那一份给了像素级的指针标注。**
合成掩膜再多也替代不了它——合成数据训出来最弱的一环恰恰就是"针 vs 刻度"
（实测针的验证 IoU 只有 0.182），而那正是真实标注最该派上用场的地方。

我这边的网络出口下不到原始包（沙箱策略挡了 bj.bcebos.com），所以用例是拿
**照着 PaddleX 文档搭出来的假目录**测的。这一点必须说清楚：用例证明的是
"转换逻辑对得上那份文档描述的结构"，不是"我在真数据上跑通了"。真包下下来
之后要人眼看 check/ 里那几张叠加图——掩膜错位在任何数字上都看不出来。
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from training.prepare_dataset import IGNORE, cmd_from_paddlex


def make_paddlex(root, n=6, *, ann_dir="annotations", palette=False,
                 bad_size=False):
    """搭一个 PaddleX 分割集的样子：图 + 同名索引 PNG。"""
    (root / "images").mkdir(parents=True, exist_ok=True)
    (root / ann_dir).mkdir(parents=True, exist_ok=True)
    for i in range(n):
        img = np.full((64, 64, 3), 180, np.uint8)
        cv2.circle(img, (32, 32), 26, (150, 150, 150), -1)
        ann = np.zeros((64, 64), np.uint8)
        ann[30:34, 32:56] = 1               # pointer
        cv2.circle(ann, (32, 32), 24, 2, 2)  # scale
        if palette:
            ann = ann * 100                  # 可视化图，不是索引图
        if bad_size and i == 0:
            ann = cv2.resize(ann, (32, 32), interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(str(root / "images" / ("m%02d.jpg" % i)), img)
        cv2.imwrite(str(root / ann_dir / ("m%02d.png" % i)), ann)
    return root


def test_converts_into_the_layout_the_trainer_already_reads(tmp_path):
    """产物必须和 gen_synthetic 完全同构，两份数据才能直接合起来训。"""
    src, out = make_paddlex(tmp_path / "px"), tmp_path / "out"
    assert cmd_from_paddlex(src, out) == 0
    imgs = list((out / "images").rglob("*.jpg"))
    assert imgs, "一张都没转出来"
    for im in imgs:
        split = im.parent.name
        assert (out / "masks" / split / (im.stem + ".png")).exists()
        assert (out / "labels" / split / (im.stem + ".txt")).exists()


def test_class_indices_are_remapped_to_this_projects_convention(tmp_path):
    """**两边类别对不齐，这是接的时候唯一要动脑子的地方。**

        本项目   background(0) face(1) needle(2) ticks(3)
        PaddleX  background(0) pointer(1) scale(2)

    映射错一位不会报错，只会让模型学成"针即刻度"，而且训练指标看着正常。
    """
    src, out = make_paddlex(tmp_path / "px"), tmp_path / "out"
    cmd_from_paddlex(src, out)
    m = cv2.imread(str(next((out / "masks").rglob("*.png"))), 0)
    assert (m == 2).any(), "pointer 没有映射成 needle(2)"
    assert (m == 3).any(), "scale 没有映射成 ticks(3)"
    assert not (m == 1).any(), "PaddleX 没有盘面标注，不该凭空冒出 face(1)"


def test_paddlex_background_is_ignored_by_default(tmp_path):
    """PaddleX 的图是表盘紧裁剪，它的"背景"其实大半是盘面，但边角又是真背景。

    分不开就别硬分：默认映射成 255，这些像素不进损失函数。于是分工是清楚的
    ——针与刻度从真实数据学，盘面与背景从合成数据学。
    """
    src, out = make_paddlex(tmp_path / "px"), tmp_path / "out"
    cmd_from_paddlex(src, out)
    m = cv2.imread(str(next((out / "masks").rglob("*.png"))), 0)
    assert (m == IGNORE).any()


@pytest.mark.parametrize("bg,want", [("face", 1), ("background", 0)])
def test_background_mapping_is_overridable(tmp_path, bg, want):
    src, out = make_paddlex(tmp_path / "px"), tmp_path / ("o_" + bg)
    cmd_from_paddlex(src, out, background=bg)
    m = cv2.imread(str(next((out / "masks").rglob("*.png"))), 0)
    assert (m == want).any() and not (m == IGNORE).any()


def test_ignored_pixels_are_skipped_by_the_trainer(tmp_path):
    """忽略标签必须真的不进训练集，否则"忽略"只是个说法。"""
    from training.train_segmenter import load_split
    src, out = make_paddlex(tmp_path / "px", n=8), tmp_path / "out"
    cmd_from_paddlex(src, out)
    rng = np.random.default_rng(0)
    try:
        _X, y = load_split(out, "train", rng)
    except SystemExit:
        pytest.skip("这一批随机划分后 train 里没有含指针的 ROI")
    assert set(np.unique(y)) <= {0, 1, 2, 3}, "忽略标签混进了训练集"
    assert IGNORE not in set(np.unique(y))


def test_a_whole_image_box_is_written(tmp_path):
    """PaddleX 的图是单块表的紧裁剪，检测框就是整张图。

    缺了这个文件 crops_of() 会把整帧跳过——一张都训不到，而且不报错。
    """
    src, out = make_paddlex(tmp_path / "px"), tmp_path / "out"
    cmd_from_paddlex(src, out)
    txt = next((out / "labels").rglob("*.txt")).read_text(encoding="utf-8")
    assert txt.split() == ["0", "0.5", "0.5", "1.0", "1.0"]


def test_a_palette_png_is_refused_not_silently_converted(tmp_path):
    """**取值不是 {0,1,2} 就一律不转。**

    可视化用的调色板 PNG（0/100/200 那种）硬转会得到一份看起来完全正常、
    类别却全错的标注——这种错要等模型训完、指标莫名其妙才有人回头查。
    """
    src, out = make_paddlex(tmp_path / "px", palette=True), tmp_path / "out"
    assert cmd_from_paddlex(src, out) == 2
    assert not list((out / "masks").rglob("*.png"))


def test_mismatched_annotation_size_is_skipped_and_counted(tmp_path):
    src, out = make_paddlex(tmp_path / "px", n=6, bad_size=True), tmp_path / "out"
    assert cmd_from_paddlex(src, out) == 0
    assert len(list((out / "images").rglob("*.jpg"))) == 5


def test_alternative_annotation_directory_names_are_found(tmp_path):
    """PaddleX 各版本的目录名不完全一致，写死一种会白等一个"找不到"。"""
    for d in ("masks", "SegmentationClass", "gt"):
        src = make_paddlex(tmp_path / ("px_" + d), ann_dir=d)
        assert cmd_from_paddlex(src, tmp_path / ("o_" + d)) == 0


def test_a_missing_download_says_what_to_do(tmp_path, capsys):
    assert cmd_from_paddlex(tmp_path / "nope", tmp_path / "out") == 2
    assert "下载" in capsys.readouterr().out


def test_check_overlays_are_written_for_human_review(tmp_path):
    """掩膜错位在任何数字上都看不出来，只有画出来才看得见。"""
    src, out = make_paddlex(tmp_path / "px"), tmp_path / "out"
    cmd_from_paddlex(src, out)
    assert list((out / "check").glob("*.jpg"))


# ------------------------------------------------------------ train/val 泄漏
#
# 盯的是一处**不报错、只让指标虚高**的失效：Roboflow 导出常把同一张原图增广成
# 多张副本，副本跨了 train/val，验证集里就有训练集的近邻。mAP 会好看，而没有
# 任何一步会红。实测巡航级 epoch 1 就有 mAP50 0.976——那个数必须先排除这个
# 可能才敢往报告里写。
#
# 判据只能按「原图」而不能按文件名：副本的文件名各不相同，比文件名一个都查不出来。

from training.prepare_dataset import cmd_check_leak, source_key


def _yolo_tree(root, train_names, val_names):
    for split, names in (("train", train_names), ("val", val_names)):
        d = root / "images" / split
        d.mkdir(parents=True, exist_ok=True)
        for n in names:
            cv2.imwrite(str(d / n), np.zeros((8, 8, 3), np.uint8))
    return root


def test_augmented_copies_of_one_photo_share_a_source_key():
    """同一张原图的两个增广副本，文件名不同但 source_key 必须相同。"""
    a = "IMG_0042_jpg.rf.5c1a9f0e8b7d4a2c9e6f1b3d8a4c7e20.jpg"
    b = "IMG_0042_jpg.rf.ffffffffffffffffffffffffffffffff.jpg"
    assert source_key(a) == source_key(b) == "IMG_0042_jpg"


def test_non_roboflow_names_only_collide_when_actually_identical():
    """不是 Roboflow 导出的名字，只有完全同名才算同一张——不能误报。"""
    assert source_key("cabinet_01.jpg") != source_key("cabinet_02.jpg")
    assert source_key("cabinet_01.jpg") == source_key("cabinet_01.png")


def test_a_clean_split_passes(tmp_path, capsys):
    _yolo_tree(tmp_path,
               ["A_jpg.rf.%032x.jpg" % 1, "B_jpg.rf.%032x.jpg" % 2],
               ["C_jpg.rf.%032x.jpg" % 3])
    assert cmd_check_leak(tmp_path) == 0
    assert "PASS" in capsys.readouterr().out


def test_a_copy_of_the_same_photo_in_both_splits_is_caught(tmp_path, capsys):
    """train 和 val 各有一张来自 A 的副本——文件名完全不同，但必须抓出来。"""
    _yolo_tree(tmp_path,
               ["A_jpg.rf.%032x.jpg" % 1, "B_jpg.rf.%032x.jpg" % 2],
               ["A_jpg.rf.%032x.jpg" % 9])
    assert cmd_check_leak(tmp_path) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "A_jpg" in out


def test_it_reports_how_much_of_val_is_tainted(tmp_path, capsys):
    """报告的是「val 有多少比例被牵连」——决定这批指标还能不能用。"""
    _yolo_tree(tmp_path,
               ["A_jpg.rf.%032x.jpg" % 1],
               ["A_jpg.rf.%032x.jpg" % 2, "Z_jpg.rf.%032x.jpg" % 3])
    assert cmd_check_leak(tmp_path) == 1
    assert "1/2" in capsys.readouterr().out          # 一半的验证集被污染


def test_a_missing_yolo_dir_says_what_to_run(tmp_path, capsys):
    assert cmd_check_leak(tmp_path / "nope") == 1
    assert "--to-yolo" in capsys.readouterr().out
