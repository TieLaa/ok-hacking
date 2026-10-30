"""MCP 入口：养成-训练循环（TrainingTask，跑完整一整轮）。

用法（MCP 的 run_task 提交本文件绝对路径）：

    python -m ok mcp --config src.config:config
    # 然后 run_task("<仓库>/tools/mcp/training.py")

和 GUI/CLI 完全同一套业务代码：reset 回大厅 → 校验 → 跑 TrainingTask.run()。
不做步数截断；想限步就设环境变量 OK_MCP_STEPS（写进本次运行 config，跑完还原）。
"""

import os

from ok import BaseTask

from src.tasks.TrainingTask import TrainingTask

from _harness import borrow, run_registered

STEPS = os.environ.get("OK_MCP_STEPS")


class Task(BaseTask):

    def reset_to_start(self):
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            return task.reset_to_start()

    def verify_start_state(self):
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            return task.verify_start_state()

    def run(self):
        overrides = {}
        if STEPS:
            overrides["单局最大操作数"] = int(STEPS)
        # 安全阀：MCP 运行永远不允许删档，除非显式开了 OK_MCP_ALLOW_DELETE=1
        if os.environ.get("OK_MCP_ALLOW_DELETE") != "1":
            overrides["新一局删档"] = False

        task, result = run_registered(self, TrainingTask, overrides)
        assert task._steps >= 1, "训练任务一拍都没跑起来（界面不认识？）"
        report = {
            "steps": task._steps,
            "last_handler": task._last_state,
            "style": task.style,
            "style_known": task.style_known,
            "in_lobby": task.is_lobby(),
            "run_returned": result,
            "attempt_dir": task.attempt_dir,
        }
        self.log_info(f"训练结果：{report}")
        return report
