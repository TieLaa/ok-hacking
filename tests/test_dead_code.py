"""死代码门禁（CI 里靠它兜底）。

为什么放进 tests：`build.yml` 只跑 `tests/*.py`，把这个门禁做成用例，
删功能后留下的残渣（未用导入 / 死函数 / 无对应控件的 QSS / 没人读的布局键）
一出现就直接红，不用等下次人工清理。

规则与豁免见 `tools/check_dead_code.py` 和 `tools/dead_code_allowlist.txt`。
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_dead_code import check_all  # noqa: E402


class DeadCodeGate(unittest.TestCase):

    def test_no_dead_code(self):
        problems = check_all()
        self.assertEqual(
            problems, [],
            "发现死代码（故意保留的写进 tools/dead_code_allowlist.txt）：\n"
            + "\n".join(problems))
