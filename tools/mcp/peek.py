"""只读窥屏：OCR 当前帧 + 判页面，不点任何按钮。

用法：
    python tools/mcp/client.py tools/mcp/peek.py
"""

import json

from ok import BaseTask

from src.tasks.TrainingTask import TrainingTask
from src.tasks.training.pages import classify_page

from _harness import borrow


class Task(BaseTask):

    def reset_to_start(self):
        return True

    def verify_start_state(self):
        return True

    def run(self):
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            texts = task._ocr_region()
            assert texts, "窥屏失败：这一帧没有识别到任何文字"
            page, candidates = classify_page(task, texts)
            task.attempt_dump("peek", texts)
            report = {
                "page": page,
                "candidates": list(candidates),
                "top_bar": task.top_bar(),
                "texts": [t.name.strip() for t in texts if t.name.strip()],
                "attempt_dir": getattr(task, "attempt_dir", None) or task.evidence.root,
            }
            self.log_info("窥屏结果：" + json.dumps(report, ensure_ascii=False))
            return report
