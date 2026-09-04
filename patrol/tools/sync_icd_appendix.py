#!/usr/bin/env python3
"""把 ICD 附录 D 的内嵌 Schema 副本按 ``patrol/schemas/`` 重新生成。

    python -m patrol.tools.sync_icd_appendix          # 写回
    python -m patrol.tools.sync_icd_appendix --check  # 只报告，不改（CI 用）

**为什么需要这个工具。** 附录 D 是五份 Schema 的第二份拷贝，改 Schema 必须同时
改附录，否则 ``validate.py`` 第 6 项会红。原来这件事靠手工，而手工同步一份
1900 行文档里的五个 JSON 块，漏一处是迟早的——D3 评审之前它就漏了五处
（A1 的 PTZ_RATE、A3 的辅视角角色、A4 的 quality、B3 的视频角色、D1 的制动上限），
靠 ``validate.py`` 的 ALLOWED_DRIFT 挂着账才没出事。

**为什么不干脆删掉附录 D。** 因为 ICD 是交付给外部的接口文档，收件人不一定拿得到
仓库。附录必须自洽可读。所以保留副本，但让它由脚本生成、由 ``validate.py`` 校验。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from patrol.common import messages as M

REPO = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO / "patrol" / "schemas"

#: 附录 D 各小节的顺序与标题，与 ICD 正文一致。
SECTIONS = [
    ("D.1", "detection_event.schema.json", "IF-1　DetectionEvent"),
    ("D.2", "control_command.schema.json", "IF-2　ControlCommand"),
    ("D.3", "command_ack.schema.json", "IF-2　CommandAck"),
    ("D.4", "status_report.schema.json", "IF-3　StatusReport"),
    ("D.5", "evidence_package.schema.json", "IF-4　EvidencePackage"),
]


def icd_path() -> Path:
    """按 glob 找 ICD，这样升版本号改文件名时不用改代码。"""
    hits = sorted((REPO / "docs").glob("ICD-RK3576-PATROL-v*.md"))
    if not hits:
        raise FileNotFoundError("docs/ 下找不到 ICD-RK3576-PATROL-v*.md")
    if len(hits) > 1:
        raise RuntimeError("docs/ 下有多份 ICD，先删掉旧的: %s"
                           % ", ".join(p.name for p in hits))
    return hits[0]


def _block(fn: str) -> str:
    d = json.loads((SCHEMA_DIR / fn).read_text(encoding="utf-8"))
    return json.dumps(d, ensure_ascii=False, indent=2)


def render_appendix() -> str:
    out = ["""## 附录 D　JSON Schema 全文

以下五份 Schema 由 `patrol/tools/sync_icd_appendix.py` 从 `patrol/schemas/` 直接
生成，`validate.py` 第 6 项会按 `json.loads` 后深比较校验（差异清单 D4：原文要求
「逐字节一致」，但 markdown 围栏缩进与行尾空白会让它误报，误报会训练出「红了就手工
改一下附录」的习惯，反而削弱这条检查）。

**改 Schema 之后跑一次 `python -m patrol.tools.sync_icd_appendix` 即可**，不要手工改本附录。

Draft 2020-12。所有对象都带 `additionalProperties: false`，未定义的字段一律不接受。
`evidence_package` 的 `l2_reading` 跨文件 `$ref` 了 `detection_event` 的定义（D3），
两份 Schema 的 `$id` 就是为此而设。
"""]
    for num, fn, caption in SECTIONS:
        out.append(f"\n### {num}　`{fn}`\n\n{caption}\n\n```json\n{_block(fn)}\n```\n")
    return "".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="同步 ICD 附录 D 的内嵌 Schema")
    ap.add_argument("--check", action="store_true", help="只检查是否已同步，不写回")
    a = ap.parse_args(argv)

    path = icd_path()
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^## 附录 D　JSON Schema 全文$", text, re.M)
    if not m:
        print("找不到「## 附录 D　JSON Schema 全文」这一节", file=sys.stderr)
        return 2
    # 附录 D 是文档最后一节；若之后还有内容，按下一个一级标题截断。
    tail = re.search(r"^## ", text[m.end():], re.M)
    end = len(text) if tail is None else m.end() + tail.start()

    new = text[:m.start()] + render_appendix() + text[end:]
    if new == text:
        print("附录 D 已与 patrol/schemas/ 一致，无需改动")
        return 0
    if a.check:
        print("附录 D 与 patrol/schemas/ 不一致，跑一次本脚本（不带 --check）同步",
              file=sys.stderr)
        return 1
    path.write_text(new, encoding="utf-8")
    print("已按 %s 重新生成 %s 的附录 D（五份 Schema：%s）"
          % (SCHEMA_DIR.relative_to(REPO), path.name,
             "、".join(fn.split(".")[0] for _, fn, _ in SECTIONS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
