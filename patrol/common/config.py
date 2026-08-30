"""配置加载。

配置分档案（profile）：configs/system.yaml 是主配置，其中 driver_mode
决定加载桩还是真机。差异清单 A1–A4 四条争议全部实现为配置开关，默认取
推荐的一侧，评审时改一行即可切到另一侧做对比演示。
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs"


def _deep_merge(base: dict, override: dict) -> dict:
    """override 覆盖 base，嵌套字典逐层合并而非整块替换。"""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    """点号取值的只读配置。

        cfg = Config.load()
        cfg.get("mission.servo.mode")          -> "pid"
        cfg.get("gateway.limits.creep_max_m")  -> KeyError（网关常量不从配置读）
    """

    def __init__(self, data: dict[str, Any]):
        self._d = data

    # -- 构造 ----------------------------------------------------------
    @classmethod
    def load(cls, path: str | os.PathLike | None = None,
             overrides: dict | None = None) -> "Config":
        p = Path(path) if path else CONFIG_DIR / "system.yaml"
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # includes: 主配置可引入其他 yaml，后引入的覆盖先引入的
        for inc in data.pop("includes", []) or []:
            inc_path = (p.parent / inc).resolve()
            with open(inc_path, encoding="utf-8") as f:
                data = _deep_merge(yaml.safe_load(f) or {}, data)
        if overrides:
            data = _deep_merge(data, overrides)
        return cls(data)

    # -- 取值 ----------------------------------------------------------
    def get(self, dotted: str, default: Any = ...) -> Any:
        node: Any = self._d
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is ...:
                    raise KeyError("配置项不存在: %s" % dotted)
                return default
            node = node[part]
        return node

    def section(self, dotted: str) -> "Config":
        return Config(self.get(dotted, {}))

    def as_dict(self) -> dict:
        return copy.deepcopy(self._d)

    def __contains__(self, dotted: str) -> bool:
        return self.get(dotted, None) is not None

    def __repr__(self) -> str:
        return "Config(%s)" % ", ".join(sorted(self._d))
