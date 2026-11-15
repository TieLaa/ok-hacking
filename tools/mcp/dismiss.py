"""MCP 一次性动作：把挡在前面的提示页点掉，直到出现「大厅 / 已知流程页」。

为什么需要：`目标达成`、`请选择1个技能` 这类页面既不是大厅也不是已知流程页，
`TrainingTask.reset_to_start()` 会返回 False，任务根本起不来。普通 shell 的点击
不一定能落到游戏窗口上（提权/UIPI），走 MCP 才是同一套输入通道。

规则：每拍先看有没有「确定」（技能页、确认弹窗都要它），没有就点屏幕中央；
一旦 `is_startable()` 成立就停。最多 15 拍。

用法：MCP `run_task` 提交本文件，跑完看日志里的 `dismiss N：` 那几行。
"""

from ok import BaseTask

from src.tasks.TrainingTask import TrainingTask

from _harness import borrow


class Task(BaseTask):

    def reset_to_start(self):
        return True

    def verify_start_state(self):
        assert self.next_frame() is not None, "起始校验失败：拿不到画面"
        return True

    def run(self):
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            for i in range(15):
                texts = task._ocr_region()
                names = [t.name.strip() for t in texts if t.name.strip()]
                if task.is_startable(texts):
                    self.log_info(f"dismiss 到第 {i} 拍已经是已知页面：" + "/".join(names[:8]))
                    return True
                confirm = next((t for t in texts if t.name.strip() == "确定"), None)
                if confirm is not None:
                    task.click_box(confirm, after_sleep=1.0)
                    action = "确定"
                else:
                    task.click(0.5, 0.55, after_sleep=1.2)
                    action = "屏幕中央"
                self.log_info(f"dismiss {i + 1}：点{action}｜" + "/".join(names[:8]))
            self.log_warning("dismiss 15 拍还没到已知页面")
            return False
