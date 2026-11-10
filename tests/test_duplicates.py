"""重复代码门禁（CI 里靠它兜底）。

为什么放进 tests：`build.yml` 只跑 `tests/*.py`，把这个门禁做成用例，
新增的「同一块代码抄两遍」一出现就直接红，不用等人工翻。

规则与豁免见 `tools/check_duplicates.py` 和 `tools/duplicates_allowlist.txt`。
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_duplicates import MIN_LINES, check_all  # noqa: E402


class DuplicateCodeGate(unittest.TestCase):

    def test_no_duplicate_blocks(self):
        problems = check_all()
        detail = []
        for places in problems:
            detail.append("--- %d 处 ---" % len(places))
            detail.extend(f"  {rel}:{ln}  [{fn}]  {first[:60]}"
                          for rel, ln, fn, first in places[:6])
        self.assertEqual(
            problems, [],
            f"发现 {MIN_LINES} 行以上的重复代码块（确实需要就写进 "
            "tools/duplicates_allowlist.txt）：\n" + "\n".join(detail))
