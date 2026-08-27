"""在图上写中文。

**OpenCV 画不了中文。**`cv2.putText` 用的是 Hershey 矢量字体，只有 ASCII；
遇到汉字会画成一串问号或空框。这件事不会报错，只会让预览窗口和存下来的
证据图上出现一片乱码——而这些图正是要拿去答辩、要给评审看的。

所以这里绕一下：有 Pillow 和中文字体就用 Pillow 渲染，两者缺一就退回
`cv2.putText`，并把中文换成事先备好的 ASCII 说法（不是丢掉——丢掉会让
"达标 / 需复核"这类关键判断在图上彻底消失）。

字体按平台常见路径找，找不到就降级。**不把字体打进仓库**：中文字体动辄
十几 MB，而且授权各不相同，为了一个预览工具背这个包不划算。
"""
from __future__ import annotations

import functools
import os

import cv2
import numpy as np

#: 常见中文字体位置，按平台排开。第一个找得到的就用。
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/System/Library/Fonts/PingFang.ttc",                       # macOS
    "/System/Library/Fonts/STHeiti Light.ttc",
    "C:/Windows/Fonts/msyh.ttc",                                # Windows
    "C:/Windows/Fonts/simhei.ttf",
)

#: 没有中文字体时的退路。**只覆盖会影响判断的词**，其余中文直接删掉。
#: 宁可英文也不要问号方块——后者会让人以为程序出错了。
_ASCII_FALLBACK = {
    "达标": "OK", "需复核": "VERIFY", "暂停": "PAUSE", "恢复巡航": "RESUME",
    "转到": "PTZ", "转速": "RATE", "蠕动前进": "CREEP", "去观察位": "GOTO",
    "小车": "CAR", "云台": "PTZ", "心跳": "HB", "拒绝": "REJECTED",
    "安全层已介入": "SAFETY ACTIVE", "指令": "CMD", "读数": "READ",
    "带内": "IN-BAND", "出带": "OUT-OF-BAND", "视场": "hfov", "电量": "batt",
    "车": "car", "未过": "failed", "证据不足": "insufficient",
    "一致": "agree", "冲突": "conflict",
}


@functools.lru_cache(maxsize=1)
def _font_path() -> str | None:
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


@functools.lru_cache(maxsize=32)
def _font(size: int):
    """按字号缓存。每帧新建 FreeTypeFont 会成为预览循环里最大的开销。"""
    path = _font_path()
    if path is None:
        return None
    try:
        from PIL import ImageFont
        return ImageFont.truetype(path, int(size))
    except Exception:                                          # noqa: BLE001
        return None


def cjk_available() -> bool:
    """当前环境能不能真的写中文。工具启动时报一句，省得让人对着乱码猜。"""
    return _font(20) is not None


def to_ascii(text: str) -> str:
    for zh, en in _ASCII_FALLBACK.items():
        text = text.replace(zh, en)
    return "".join(ch if ord(ch) < 128 else "" for ch in text).strip()


def has_cjk(text: str) -> bool:
    return any(ord(ch) > 127 for ch in text)


def draw_text(img: np.ndarray, text: str, org: tuple[int, int], *,
              size: int = 20, color=(20, 20, 20), stroke=(255, 255, 255),
              stroke_width: int = 2) -> np.ndarray:
    """在 img 上写一行字（就地修改并返回）。org 是**左上角**，不是基线。

    用左上角而不是 OpenCV 的基线：叠加层是一行行往下摞的，按左上角排版才能
    直接用"上一行底边 + 行距"，不必先问字体要 ascent。

    描边默认开着。预览画面的背景是配电柜的浅灰和柜门的深色，纯色文字总有
    一半看不清；描边比"挑一个好颜色"可靠得多。
    """
    if not text:
        return img
    f = _font(size) if has_cjk(text) else None
    if f is None:
        # 退回 OpenCV：换 ASCII 说法，并把左上角换算成基线
        s = to_ascii(text) if has_cjk(text) else text
        scale = size / 30.0
        if stroke_width:
            cv2.putText(img, s, (org[0], org[1] + size), cv2.FONT_HERSHEY_SIMPLEX,
                        scale, stroke, stroke_width + 2, cv2.LINE_AA)
        cv2.putText(img, s, (org[0], org[1] + size), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, color, max(1, stroke_width), cv2.LINE_AA)
        return img
    from PIL import Image, ImageDraw
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    d.text(org, text, font=f, fill=tuple(int(c) for c in reversed(color)),
           stroke_width=int(stroke_width),
           stroke_fill=tuple(int(c) for c in reversed(stroke)))
    img[:, :] = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    return img


def text_size(text: str, size: int = 20) -> tuple[int, int]:
    f = _font(size) if has_cjk(text) else None
    if f is None:
        s = to_ascii(text) if has_cjk(text) else text
        (w, h), _ = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, size / 30.0, 2)
        return w, h
    box = f.getbbox(text)
    return int(box[2] - box[0]), int(box[3] - box[1])


def panel(img: np.ndarray, x: int, y: int, w: int, h: int, *,
          alpha: float = 0.62, color=(24, 26, 30)) -> None:
    """半透明底板。文字压在画面上永远有一部分看不清，垫一层就都清楚了。"""
    x2, y2 = min(img.shape[1], x + w), min(img.shape[0], y + h)
    x, y = max(0, x), max(0, y)
    if x2 <= x or y2 <= y:
        return
    roi = img[y:y2, x:x2]
    img[y:y2, x:x2] = cv2.addWeighted(
        roi, 1.0 - alpha, np.full_like(roi, np.array(color, np.uint8)), alpha, 0)
