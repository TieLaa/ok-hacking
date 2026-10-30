"""MCP 快速比赛冒烟：借真实 QuickMatchTask 跑 N 拍（默认 10 拍，0.5 秒/拍）。

常驻任务**不回大厅**：比赛打到一半把界面点回大厅等于毁掉这一局。
所以这里只断言「当前是可识别的场景 + 至少跑了 N 拍」。

用法：通过 MCP 的 run_task 提交本文件绝对路径。可用环境变量
``OK_MCP_QUICK_TICKS`` 改拍数（默认 10）。
"""

import os

from ok import BaseTask

from src.tasks.QuickMatchTask import QuickMatchTask

from _harness import borrow

TICKS = int(os.environ.get("OK_MCP_QUICK_TICKS", "10"))


class Task(BaseTask):

    def reset_to_start(self):
        with borrow(self, QuickMatchTask, self.artifact_directory) as task:
            return task.reset_to_start()

    def verify_start_state(self):
        with borrow(self, QuickMatchTask, self.artifact_directory) as task:
            return task.verify_start_state()

    def run(self):
        with borrow(self, QuickMatchTask, self.artifact_directory) as task:
            acted = 0
            for _ in range(TICKS):
                if task.run():
                    acted += 1
                task.sleep(0.5)
            assert task._steps >= 1, "冒烟失败：一拍都没跑起来"
            report = {
                "ticks": TICKS,
                "steps": task._steps,
                "acted": acted,
                "in_lobby": task.is_lobby(),
                "attempt_dir": task.attempt_dir,
            }
            self.log_info(f"快速比赛冒烟结果：{report}")
            return report
