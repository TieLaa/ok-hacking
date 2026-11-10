"""存证落盘契约：按局分目录、命名标记、清理安全阀、点击流水带 round。

这组测试不碰游戏、不读真实 `logs/`：全部在 tmp 目录里造一个「产物根」。
为什么值得单测（2026-11-10 归口改造）：
  - 局目录是**暂停/继续不换目录、每局才换**的判据，错了会把一局拆成两半；
  - 清理是 `shutil.rmtree`，安全阀错了会删掉 clicks.jsonl / reports/；
  - 这两条都是事后无法从日志反推的。
"""

import json
import os
import shutil
import tempfile
import time
import unittest

import numpy as np

from src.tasks import evidence


class RoundDirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ok-evidence-")
        self._click_log = evidence.CLICK_LOG
        evidence.CLICK_LOG = os.path.join(self.tmp, "clicks.jsonl")
        self.sink = evidence.EvidenceSink()
        self.sink.begin_run(self.tmp)
        self.frame = np.zeros((120, 160, 3), dtype=np.uint8)

    def tearDown(self):
        evidence.CLICK_LOG = self._click_log
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_round_dir_has_four_subs_and_meta(self):
        path = self.sink.begin_round(captain="野上泷", entry="GUI")
        self.assertTrue(os.path.isdir(path))
        for sub in evidence.ROUND_SUBS:
            self.assertTrue(os.path.isdir(os.path.join(path, sub)), sub)
        with open(os.path.join(path, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta["captain"], "野上泷")
        self.assertEqual(meta["entry"], "GUI")
        self.assertFalse(meta["handover"])
        self.assertTrue(evidence.ROUND_RE.match(os.path.basename(path)))

    def test_handover_and_round_no_markers(self):
        path = self.sink.begin_round(captain="", handover=True, round_no=3)
        name = os.path.basename(path)
        self.assertIn("接手", name)          # 没指定队长 = 快速接手
        self.assertIn("第3局", name)         # 队列模式才有局序号

    def test_single_round_has_no_round_no(self):
        name = os.path.basename(self.sink.begin_round(captain="近江奏", round_no=1))
        self.assertNotIn("第1局", name)

    def test_pausing_does_not_switch_dir(self):
        """暂停/继续是原地挂起（flow.wait_while_paused），目录必须不变。"""
        first = self.sink.begin_round(captain="近江奏")
        self.sink.dump_unknown([], "while_paused", self.frame, step=3)
        self.assertEqual(first, self.sink.round_dir)

    def test_new_round_gets_new_dir(self):
        first = self.sink.begin_round(captain="近江奏")
        time.sleep(1.05)                     # 名字只精确到秒
        second = self.sink.begin_round(captain="近江奏")
        self.assertNotEqual(first, second)

    def test_evidence_lands_inside_round(self):
        self.sink.begin_round(captain="近江奏")
        base = self.sink.dump_unknown([], "unknown", self.frame, step=7)
        self.assertTrue(base.startswith(self.sink.round_dir))
        self.assertTrue(os.path.isfile(base + ".jpg"))
        self.assertTrue(os.path.isfile(base + ".txt"))
        folder = self.sink.dump_debug([], "stuck", self.frame, summary="step=1")
        for name in ("frame.jpg", "ocr.txt", "actions.txt", "threads.txt"):
            self.assertTrue(os.path.isfile(os.path.join(folder, name)), name)

    def test_no_round_falls_back_to_pre(self):
        """MCP 的 reset/verify 阶段就出错时还没有局目录，存证落 <root>/pre。"""
        base = self.sink.dump_unknown([], "reset", self.frame, step=1)
        self.assertIn(os.sep + evidence.PRE_DIR + os.sep, base)

    def test_click_line_carries_round(self):
        self.sink.begin_round(captain="近江奏")
        self.sink.click({"at": 1.0, "name": "下一步"})
        with open(evidence.CLICK_LOG, encoding="utf-8") as f:
            line = json.loads(f.read().strip())
        self.assertEqual(line["round"], self.sink.round_id)
        self.assertEqual(line["name"], "下一步")

    def test_meta_records_run_log_offsets(self):
        self.sink.begin_round(captain="近江奏")
        self.sink.end_round("收工", steps=42)
        with open(os.path.join(self.sink.round_dir, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta["steps"], 42)
        self.assertEqual(meta["end_reason"], "收工")
        self.assertIn("run_log_start", meta)
        self.assertIn("run_log_end", meta)


class PruneSafety(unittest.TestCase):
    """清理只许删「名字像局ID + 有四个子目录之一」的整局目录。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ok-prune-")
        self._click_log = evidence.CLICK_LOG
        evidence.CLICK_LOG = os.path.join(self.tmp, "clicks.jsonl")
        self.sink = evidence.EvidenceSink()
        self.sink.begin_run(self.tmp)

    def tearDown(self):
        evidence.CLICK_LOG = self._click_log
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _age(self, path, days):
        old = time.time() - days * 86400
        os.utime(path, (old, old))

    def _fake_round(self, name, sub="events"):
        path = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(path, sub), exist_ok=True)
        open(os.path.join(path, sub, "x.jpg"), "wb").close()
        self._age(path, 30)
        return path

    def test_old_round_is_deleted(self):
        path = self._fake_round("1110-091635_野上泷")
        self.sink.prune(3)
        self.assertFalse(os.path.exists(path))

    def test_fresh_round_is_kept(self):
        old = self._fake_round("1110-091635_野上泷")
        fresh = os.path.join(self.tmp, "1110-120000_近江奏")
        os.makedirs(os.path.join(fresh, "events"), exist_ok=True)
        self.sink.prune(3)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))

    def test_non_round_dirs_and_files_survive(self):
        reports = os.path.join(self.tmp, "reports")
        os.makedirs(reports)
        open(os.path.join(self.tmp, "clicks.jsonl"), "a", encoding="utf-8").close()
        pre = os.path.join(self.tmp, evidence.PRE_DIR)
        os.makedirs(os.path.join(pre, "events"))
        for p in (reports, pre, os.path.join(self.tmp, "connections")):
            if not os.path.exists(p):
                os.makedirs(p)
            self._age(p, 30)
        self._age(os.path.join(self.tmp, "clicks.jsonl"), 30)
        self.sink.prune(3)
        for p in (reports, pre, os.path.join(self.tmp, "connections"),
                  os.path.join(self.tmp, "clicks.jsonl")):
            self.assertTrue(os.path.exists(p), p)

    def test_dir_without_evidence_subs_is_kept(self):
        """名字像局ID 但没有四个子目录 = 不是局目录，绝不整目录删。"""
        path = os.path.join(self.tmp, "1110-091635_别的东西")
        os.makedirs(os.path.join(path, "something"))
        self._age(path, 30)
        self.sink.prune(3)
        self.assertTrue(os.path.exists(path))

    def test_zero_days_disables_prune(self):
        path = self._fake_round("1110-091635_野上泷")
        self.sink.prune(0)
        self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
