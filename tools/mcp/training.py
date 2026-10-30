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

# 本次运行要选的队长：由外部写 logs/mcp/next_captain.json 指定（不用重启 runtime）。
# 内容示例：{"captain": "真崎辉世", "allow_delete": false}
CAPTAIN_FILE = os.path.join("logs", "mcp", "next_captain.json")


def _next_captain():
    try:
        import json
        with open(CAPTAIN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                # noqa: BLE001
        return {}


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
        # 本次运行的队长（外部文件指定，跑完不写回 configs）
        wanted = _next_captain()
        if wanted.get("captain"):
            overrides["指定队长"] = wanted["captain"]
            self.log_info(f"本次指定队长：{wanted['captain']}")
        # 安全阀：MCP 运行默认不允许删档，除非显式 allow_delete
        if wanted.get("allow_delete") is True:
            overrides["新一局删档"] = True
        else:
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
