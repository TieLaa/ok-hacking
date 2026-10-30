"""MCP 入口：配置演示任务（MyOneTimeTask，只打日志、不改游戏状态）。

存在的意义是让「所有已注册任务」都有对应的 MCP 入口，冒烟时能一次验完。
"""

from ok import BaseTask

from src.tasks.MyOneTimeTask import MyOneTimeTask

from _harness import borrow, run_registered


class Task(BaseTask):

    def reset_to_start(self):
        with borrow(self, MyOneTimeTask, self.artifact_directory) as task:
            return task.reset_to_start()

    def verify_start_state(self):
        with borrow(self, MyOneTimeTask, self.artifact_directory) as task:
            return task.verify_start_state()

    def run(self):
        task, result = run_registered(self, MyOneTimeTask)
        report = {"returned": result, "attempt_dir": task.attempt_dir}
        self.log_info(f"演示任务结果：{report}")
        return report
