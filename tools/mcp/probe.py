"""MCP 基线探针：回到大厅 → 断言在大厅 → 只读报告当前状态。

用法（ok-script 2.0.16+）：

    python -m ok mcp --config src.config:config        # 另开一个终端跑
    # 然后通过 MCP 调 run_task("<仓库绝对路径>/tools/mcp/probe.py")

它不点任何业务按钮（回大厅的返回箭头除外），适合作为每次改代码后的第一只探针：
先确认窗口、采集、OCR、顶栏解析都正常，再去跑真正的流程。
"""

import json

from ok import BaseTask

from src.tasks.TrainingTask import TrainingTask

from _harness import borrow


class Task(BaseTask):

    def reset_to_start(self):
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            return task.reset_to_start()

    def verify_start_state(self):
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            return task.verify_start_state()

    def run(self):
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            texts = task._ocr_region()
            assert texts, "探针失败：这一帧没有识别到任何文字"
            state = task.top_bar()
            task.attempt_dump("probe", texts)
            report = {
                "lobby": task.is_lobby(texts),
                "top_bar": state,
                "text_count": len(texts),
                "texts": [t.name.strip() for t in texts if t.name.strip()][:30],
                "attempt_dir": task.attempt_dir,
            }
            self.log_info("探针结果：" + json.dumps(report, ensure_ascii=False))
            return report
