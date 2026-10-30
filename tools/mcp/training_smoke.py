"""MCP 训练流程冒烟：借真实 TrainingTask 跑一小段（默认 30 拍）。

目的不是跑完一局，而是验证「识别 → 决策 → 点击」这条链在当前版本下没坏。
真跑一局请用 GUI 或 ``python -m ok run_task TrainingTask``。

用法：通过 MCP 的 run_task 提交本文件绝对路径。可用环境变量
``OK_MCP_SMOKE_STEPS`` 改步数（默认 30）。
"""

import os

from ok import BaseTask

from src.tasks.TrainingTask import TrainingTask

from _harness import borrow

SMOKE_STEPS = int(os.environ.get("OK_MCP_SMOKE_STEPS", "30"))


class Task(BaseTask):

    def reset_to_start(self):
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            return task.reset_to_start()

    def verify_start_state(self):
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            return task.verify_start_state()

    def run(self):
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            original_budget = task.config.get("单局最大操作数")
            original_stuck = task.config.get("卡死判定(秒)")
            task.config["单局最大操作数"] = SMOKE_STEPS
            task.config["卡死判定(秒)"] = max(6, int(original_stuck or 5))
            try:
                task.run()          # 达到步数上限会返回 False，那是预期，不算失败
            finally:
                task.config["单局最大操作数"] = original_budget
                task.config["卡死判定(秒)"] = original_stuck

            assert task._steps >= 1, "冒烟失败：一拍都没跑起来"
            assert task._steps >= min(SMOKE_STEPS, 3), (
                f"冒烟失败：只跑了 {task._steps} 拍就退出（界面不认识？）")
            report = {
                "steps": task._steps,
                "last_handler": task._last_state,
                "in_lobby": task.is_lobby(),
                "attempt_dir": task.attempt_dir,
            }
            assert (report["last_handler"] is not None
                    or report["in_lobby"]), "冒烟失败：没有任何 handler 命中"
            self.log_info(f"冒烟结果：{report}")
            return report
