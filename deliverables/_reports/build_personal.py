# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import content_personal as C
from rep_lib import REPO, build

build(os.path.join(REPO, "deliverables", "个人报告-吴明哲.docx"),
      title_lines=C.TITLE,
      cover={"kind": "个人报告", "group_no": "【小组编号】", "leader": "吴明哲",
             "major": "【专业年级】", "date": ("2026", "9", "【 】")},
      members=C.MEMBERS,
      abstract_cn=C.ABSTRACT_CN, keywords_cn=C.KEYWORDS_CN,
      title_en=C.TITLE_EN, abstract_en=C.ABSTRACT_EN, keywords_en=C.KEYWORDS_EN,
      blocks=C.BLOCKS)
