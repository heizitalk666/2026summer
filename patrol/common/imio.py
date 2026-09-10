"""能处理非 ASCII 路径的图像读写。**Windows 上 `cv2.imread` 有个静默陷阱。**

`cv2.imread` / `cv2.imwrite` 在 Windows 上走的是 ANSI API，路径里只要有一个
字符不在系统代码页里（中文目录名是最常见的情形），就会**失败并返回 None /
False，不抛异常**。而 `os.path.exists()` 对同一个路径返回 True——因为它走的是
宽字符 API。

实测（本仓库所在路径含「新增資料夾」）：

    cv2.imread('training/datasets/.../000009.jpg')          → 读到 (1080,1920,3)
    cv2.imread(str(Path(同一个文件).resolve()))              → None
    os.path.exists(那个绝对路径)                              → True

**相对路径能读、绝对路径读不到**，因为进程的工作目录已经在中文目录里面了，
相对路径拼出来的字符串不含非 ASCII。

这个坑的代价是它完全无声。`train_unet.collect_rois` 里那句

    if m is None or img is None or img.shape[:2] != m.shape[:2]:
        continue

会把 120 张 val 图**全部跳过**，最后报「一个含针 ROI 都没采到」——而真正的
原因跟掩膜、跟类别号都没关系。查这种问题会一路查到数据集上去。

所以：**仓库里凡是从磁盘读写图像的地方，一律用这里的两个函数**，不要直接调
`cv2.imread` / `cv2.imwrite`。`np.fromfile` 与 `ndarray.tofile` 走的是 Python
自己的文件 IO（宽字符），编解码交给 `cv2.imdecode` / `cv2.imencode` 在内存里做。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def imread(path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray | None:
    """读图。路径含中文也能读；读不到返回 None（与 cv2.imread 语义一致）。"""
    p = Path(path)
    try:
        buf = np.fromfile(str(p), dtype=np.uint8)
    except (OSError, ValueError):
        return None
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, flags)


def imwrite(path, img: np.ndarray, params=None) -> bool:
    """写图。路径含中文也能写；成功返回 True。

    **返回值要检查。**原来那批 `cv2.imwrite(str(d / "cruise.jpg"), ...)` 谁都
    没看返回值，于是证据包里少一张图这件事在运行时是完全静默的。
    """
    p = Path(path)
    ext = p.suffix or ".jpg"
    try:
        ok, buf = cv2.imencode(ext, img, params or [])
    except cv2.error:
        return False
    if not ok:
        return False
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        buf.tofile(str(p))
    except (OSError, ValueError):
        return False
    return True
