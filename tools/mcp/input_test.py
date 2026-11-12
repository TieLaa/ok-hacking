"""MCP 入口：输入测试（InputTestTask，点一次并验证界面变化）。

reset 回大厅、verify 断言在大厅（这个任务默认点「赛事」，需要大厅的入口按钮）。
成功条件是任务自己的返回值：界面出现期望文字 / 画面变化超过阈值。
"""

from ok import BaseTask

from src.tasks.InputTestTask import InputTestTask

from _harness import borrow, run_registered


class Task(BaseTask):

    def reset_to_start(self):
        with borrow(self, InputTestTask, self.artifact_directory) as task:
            return task.reset_to_start()

    def verify_start_state(self):
        with borrow(self, InputTestTask, self.artifact_directory) as task:
            return task.verify_start_state()

    def run(self):
        task, result = run_registered(self, InputTestTask)
        report = {"worked": bool(result),
                  "target": task.config.get("目标文字"),
                  "expect": task.config.get("期望出现文字"),
                  "attempt_dir": getattr(task, "attempt_dir", None) or task.evidence.root}
        self.log_info(f"输入测试结果：{report}")
        assert result, f"输入测试失败：点击没有生效（{report}）"
        return report
