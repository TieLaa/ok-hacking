"""MCP 入口：全国大赛（NationalCupTask）。

业务逻辑不复制：借已注册的任务实例跑 reset / verify / run。

用法：MCP run_task 提交本文件。
"""

from ok import BaseTask

from src.tasks.NationalCupTask import NationalCupTask

from _harness import borrow


class Task(BaseTask):

    def reset_to_start(self):
        with borrow(self, NationalCupTask, self.artifact_directory) as task:
            return task.reset_to_start()

    def verify_start_state(self):
        with borrow(self, NationalCupTask, self.artifact_directory) as task:
            return task.verify_start_state()

    def run(self):
        with borrow(self, NationalCupTask, self.artifact_directory) as task:
            return task.run()
