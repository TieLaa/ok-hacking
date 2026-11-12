"""MCP 入口：快速比赛（QuickMatchTask，常驻任务跑 N 拍）。

常驻任务**不回大厅**（比赛打到一半点返回会毁局），所以 reset 只确认采集可用。

用法：MCP run_task 提交本文件；拍数用环境变量 OK_MCP_TICKS（默认 10）。
"""

import os

from ok import BaseTask

from src.tasks.QuickMatchTask import QuickMatchTask

from _harness import borrow, run_registered

TICKS = int(os.environ.get("OK_MCP_TICKS", "10"))


class Task(BaseTask):

    def reset_to_start(self):
        with borrow(self, QuickMatchTask, self.artifact_directory) as task:
            return task.reset_to_start()

    def verify_start_state(self):
        with borrow(self, QuickMatchTask, self.artifact_directory) as task:
            return task.verify_start_state()

    def run(self):
        acted = 0
        for _ in range(TICKS):
            task, result = run_registered(self, QuickMatchTask)
            if result:
                acted += 1
            task.sleep(0.5)
        assert task._steps >= 1, "快速比赛一拍都没跑起来"
        report = {
            "ticks": TICKS,
            "steps": task._steps,
            "acted": acted,
            "in_lobby": task.is_lobby(),
            "attempt_dir": getattr(task, "attempt_dir", None) or task.evidence.root,
        }
        self.log_info(f"快速比赛结果：{report}")
        return report
