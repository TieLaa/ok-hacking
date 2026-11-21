"""每局训练结果记录/汇总的单元测试（2026-11-21）。"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.tasks.training import results  # noqa: E402


class FakeQueue:
    target = 9000


class FakeTask:

    def __init__(self, mcp=False, with_results=True):
        self.config = {"目标分": 9000}
        self.mcp_driven = mcp
        self._round_no = 1
        self._queue = FakeQueue()
        self._style_name = "望月透"
        self.logs = []
        self.alerts = []
        if with_results:
            self._run_results = []

    def configured_captain(self):
        return "望月透"

    def log_info(self, message, notify=False):
        self.logs.append(message)

    def log_warning(self, message, notify=False):
        self.logs.append(message)

    def alert(self, title, text):
        self.alerts.append((title, text))


class ResultsTest(unittest.TestCase):

    def test_record_and_update_same_round(self):
        task = FakeTask()
        results.begin_run(task)
        results.record(task, 7000, "S", persist=False)
        results.record(task, 7100, "SS", persist=False)
        self.assertEqual(len(task._run_results), 1)
        item = task._run_results[0]
        self.assertEqual(item["score"], 7100)
        self.assertEqual(item["rating"], "SS")
        self.assertEqual(item["target"], 9000)
        self.assertFalse(item["met"])

    def test_record_persists_immediately(self):
        """每局立刻落盘：关窗口/进程退出不丢已完成局。"""
        task = FakeTask()
        results.begin_run(task)
        with tempfile.TemporaryDirectory() as folder:
            task._results_folder = folder
            results.record(task, 7000, "S")
            path = os.path.join(
                folder, f"training_results_{task._results_run_id}.json")
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["results"][0]["score"], 7000)
            self.assertEqual(data["results"][0]["rating"], "S")

    def test_two_rounds_and_summary(self):
        task = FakeTask()
        results.begin_run(task)
        task._round_no = 1
        results.record(task, 7000, "S", persist=False)
        task._round_no = 2
        results.record(task, 9100, persist=False)
        self.assertTrue(task._run_results[1]["met"])
        text = results.summary_text(task)
        self.assertIn("第1局", text)
        self.assertIn("第2局", text)
        self.assertIn("未达标", text)
        self.assertIn("达标", text)

    def test_finish_writes_files_and_alerts_once(self):
        task = FakeTask()
        results.begin_run(task)
        results.record(task, 9100, "SS", persist=False)
        with tempfile.TemporaryDirectory() as folder:
            paths = results.finish_run(task, folder=folder)
            self.assertEqual(len(task.alerts), 1)
            self.assertTrue(os.path.exists(paths[0]))
            self.assertTrue(os.path.exists(paths[1]))
            with open(paths[0], encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["results"][0]["score"], 9100)
            self.assertEqual(data["results"][0]["rating"], "SS")
            # 第二次收尾不再弹
            results.finish_run(task, folder=folder)
            self.assertEqual(len(task.alerts), 1)

    def test_mcp_no_alert(self):
        task = FakeTask(mcp=True)
        results.begin_run(task)
        results.record(task, 9100, persist=False)
        with tempfile.TemporaryDirectory() as folder:
            results.finish_run(task, folder=folder)
        self.assertEqual(task.alerts, [])

    def test_empty_run_no_alert(self):
        task = FakeTask()
        results.begin_run(task)
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(results.finish_run(task, folder=folder))
        self.assertEqual(task.alerts, [])

    def test_quickmatch_without_results_is_ignored(self):
        task = FakeTask(with_results=False)
        self.assertIsNone(results.record(task, 9100, persist=False))


if __name__ == "__main__":
    unittest.main()
