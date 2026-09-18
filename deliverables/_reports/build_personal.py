# -*- coding: utf-8 -*-
"""个人报告：按「测控系统综合实训个人报告模板.docx」生成（与小组报告模板结构不同）。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import content_personal as C
from rep_lib import REPO
from rep_personal import build

build(os.path.join(REPO, "deliverables", "个人报告-吴明哲.docx"),
      title_lines=C.TITLE, members=C.MEMBERS, blocks=C.BLOCKS, **C.COVER)
