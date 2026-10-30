"""MCP 入口：自动战斗（AutoBattleTask 骨架，跑 N 拍）。

骨架任务只读画面、不改游戏状态，所以 reset/verify 只要求采集可用。
拍数用环境变量 OK_MCP_TICKS（默认 10）。
"""

import os

from ok import BaseTask

from src.tasks.AutoBattleTask import AutoBattleTask

from _harness import borrow, run_registered

TICKS = int(os.environ.get("OK_MCP_TICKS", "10"))


class Task(BaseTask):

    def reset_to_start(self):
        with borrow(self, AutoBattleTask, self.artifact_directory) as task:
            return task.reset_to_start()

    def verify_start_state(self):
        with borrow(self, AutoBattleTask, self.artifact_directory) as task:
            return task.verify_start_state()

    def run(self):
        fired = 0
        for _ in range(TICKS):
            task, result = run_registered(self, AutoBattleTask)
            if result:
                fired += 1
            task.sleep(0.2)
        report = {"ticks": TICKS, "fired": fired,
                  "attempt_dir": task.attempt_dir}
        self.log_info(f"自动战斗结果：{report}")
        return report
