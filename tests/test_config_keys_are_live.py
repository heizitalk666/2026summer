"""配置键必须有人读——否则配置就在撒谎。

**这个项目被同一类 bug 咬过三次**，每次的形状都一样：配置里写着一个键，
看着像个开关，实际没有任何代码读它，于是「改配置」和「改行为」脱钩：

1. `perception.l3.min_density_px` —— 配置里写着 60，催办清单据此断言
   「巡航期调用量已经压下去了」。实测 `grep -rn min_density patrol/` 零命中，
   L3 在巡航态每帧每个检出都无条件打分，帧率塌到 5.6 fps。
2. `mission.capture.clip_ring_frames` —— 代码读它，**但 configs 里根本没有这个键**，
   于是永远走 40 帧兜底；而配置里真正写着的 `uploader.video.pre_seconds`
   又没人读。配置说片段 3.0 s，实际出 4.0 s。
3. `uploader.video.post_seconds` —— 至今零引用：片段 100 % 是触发前回放，
   没有任何触发后画面。这条已在配置里标 ⚠，并列进下面的白名单。

前两条都是**事后**才发现的，而且都是在「据此写了对外结论」之后。这条测试把
它变成当场就红：任何新增的配置键，要么有人读，要么进白名单并写清为什么。

白名单里的每一条都必须在 configs 对应位置有 ⚠ 注释说明它不生效——
**两边对不上时以配置里的注释为准，它离读配置的人更近。**
"""
from __future__ import annotations

import pathlib
import re

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent

#: 明知不生效、但有理由留着的键。**加进来必须同时在 configs 里写 ⚠ 注释。**
RESERVED: dict[str, str] = {
    # A3 条件式辅视角：接口与 Schema 已冻结，首版不实现（见 docs/催办清单.md 第五节）
    "mission.capture.aux_grab_ms": "A3 接口预留，首版不实现",
    "mission.capture.aux_settle_ms": "A3 接口预留，首版不实现",
    "mission.capture.multiview_spread_limit_pct_fs": "A3 接口预留，首版不实现",
    # B3 证据包视频：pre_seconds 已接上，post_seconds 要改落盘时序才能实现
    "uploader.video.post_seconds": "触发后录制未实现，片段只有触发前回放",
    # 真机相关：硬件到位前没有代码路径
    "real.serial.kinematics.holonomic": "真底盘只发任务级指令，不自己算里程",
    "real.serial.kinematics.lx_plus_ly_m": "同上，留作真机联调对表",
    "real.serial.kinematics.motor_rpm_limit": "同上",
    "real.serial.kinematics.wheel_radius_m": "同上",
    # 推导值的配置留档
    "optics.max_observe_distance_m": "d_max 由 scene/optics.py 当场算，这里只留推导",
    "scene.lighting.color_temp_k": "渲染器只建模亮度不建模色温，留作真机标定记录",
}


def _leaf_paths(node, prefix: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            p = "%s.%s" % (prefix, k) if prefix else str(k)
            out += _leaf_paths(v, p) if isinstance(v, dict) else [p]
    return out


def _all_config_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    for f in sorted((REPO / "configs").glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for p in _leaf_paths(data):
            keys.setdefault(p, f.name)
    return keys


def _source_text() -> str:
    buf = []
    for sub in ("patrol", "cloud", "training", "tests"):
        for f in (REPO / sub).rglob("*.py"):
            if "__pycache__" in str(f):
                continue
            buf.append(f.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(buf)


def _is_referenced(path: str, src: str) -> bool:
    """整条路径出现，或最后一段作为字符串/标识符出现，都算有人读。

    宽松是有意的：`cfg.get("perception.l3")` 之后再 `.get("min_density_px")`
    这种两段式取值很常见，严格匹配整条路径会误报一片。宁可漏也不要吵——
    漏掉的那些还有 code review，而一条天天误报的测试很快就会被 -k 掉。
    """
    if path in src:
        return True
    last = path.rsplit(".", 1)[-1]
    return bool(re.search(r"['\"]%s['\"]|\b%s\b" % (re.escape(last), re.escape(last)), src))


def test_no_config_key_is_silently_dead():
    src = _source_text()
    dead = {k: f for k, f in _all_config_keys().items()
            if k not in RESERVED and not _is_referenced(k, src)}
    assert not dead, (
        "这些配置键没有任何代码读，改它们不会改变行为：\n  "
        + "\n  ".join("%-50s (%s)" % (k, f) for k, f in sorted(dead.items()))
        + "\n\n要么接上代码，要么加进本文件的 RESERVED 并在 configs 里写 ⚠ 注释。")


def test_reserved_list_has_no_stale_entries():
    """白名单不许留过期条目——键已删或已经接上代码了，就该从白名单拿掉。"""
    keys = _all_config_keys()
    gone = sorted(k for k in RESERVED if k not in keys)
    assert not gone, "白名单里这些键在 configs 里已经不存在了，请删掉：%s" % gone


@pytest.mark.parametrize("key", sorted(RESERVED))
def test_reserved_keys_are_marked_in_the_yaml(key):
    """白名单里的键，configs 里必须有 ⚠ 注释——读配置的人先看到的是配置。"""
    fname = _all_config_keys()[key]
    text = (REPO / "configs" / fname).read_text(encoding="utf-8")
    last = key.rsplit(".", 1)[-1]
    lines = text.splitlines()
    hit = [i for i, ln in enumerate(lines) if re.match(r"\s*%s\s*:" % re.escape(last), ln)]
    assert hit, "%s 在 %s 里找不到定义行" % (key, fname)
    # 往上找最多 12 行注释，要求出现 ⚠
    i = hit[0]
    window = "\n".join(lines[max(0, i - 12):i + 1])
    assert "⚠" in window, (
        "%s 在白名单里但 %s 的定义处没有 ⚠ 注释说明它不生效" % (key, fname))
