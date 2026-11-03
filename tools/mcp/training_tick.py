"""MCP 入口：**从当前界面接手**跑 N 拍（不回大厅、不校验开局）。

和 `training.py` 的区别：
    training.py    reset 回大厅 → 校验 → 跑完整一轮（新开一局用）
    training_tick  直接在当前页面接手 → 跑 N 拍就停（改完判据/卡在中间时用）

用法（MCP 的 run_task 提交本文件绝对路径）：

    python -m ok mcp --config src.config:config
    # 然后 run_task("<仓库>/tools/mcp/training_tick.py")

步数（默认 30）可用 `logs/mcp/next_steps.json` 覆盖，例如 `{"steps": 80}`。
**不用环境变量**：这个文件跑在 runtime 进程里，客户端设的 env 传不进去。
"""

import json
import os

from ok import BaseTask

from src.tasks.TrainingTask import TrainingTask

from _harness import run_registered

STEPS_FILE = os.path.join("logs", "mcp", "next_steps.json")
DEFAULT_STEPS = 30
HOT_FLAG = os.path.join("logs", "mcp", "hot_reload.json")


def _steps():
    try:
        with open(STEPS_FILE, encoding="utf-8") as f:
            return int(json.load(f).get("steps") or DEFAULT_STEPS)
    except Exception:                                # noqa: BLE001
        return DEFAULT_STEPS


def _hot_reload():
    """把 `src.tasks.training.*` 里**已经改过**的代码装进 runtime（不重启进程）。

    为什么需要：MCP runtime 是管理员进程，`reload_tasks` 只重载「任务类」，
    不会重载 `src.tasks.training.parsers / scoring / handlers` 这些深层模块。
    改了解析/判据/新 handler 之后，正常做法是重启 runtime（要 UAC，见
    `tools/restart_mcp_runtime.ps1`）；本函数是「不重启」时的临时通道，
    **只在 `logs/mcp/hot_reload.json` 存在时执行**，正式跑请重启。

    做法：reload 模块拿到新的类，再把新方法 `setattr` 回**正在使用的旧类对象**
    （TrainingTask 的 MRO 指向的是旧类，不 setattr 就还是旧行为），
    最后把 `HANDLERS` 换成重载后的新元组。
    """
    import importlib

    import src.tasks.BaseGirlsTask as base_mod
    import src.tasks.training.parsers as parsers_mod
    import src.tasks.training.scoring as scoring_mod
    import src.tasks.training.handlers as handlers_mod
    import src.tasks.ui.labels as labels_mod      # noqa: F401  （先刷常量）

    old_base = base_mod.BaseGirlsTask
    old_parsers = parsers_mod.TrainingParsersMixin
    old_scoring = scoring_mod.TrainingScoringMixin

    importlib.reload(labels_mod)
    importlib.reload(base_mod)
    importlib.reload(parsers_mod)
    importlib.reload(scoring_mod)
    importlib.reload(handlers_mod)

    patched = []
    for name in ("card_quality_color", "quality_cards"):
        setattr(old_parsers, name, getattr(parsers_mod.TrainingParsersMixin, name))
        patched.append(name)
    for name in ("QUALITY_RANK", "quality_card_order"):
        setattr(old_scoring, name, getattr(scoring_mod.TrainingScoringMixin, name))
        patched.append(name)
    setattr(old_base, "dump_unmark", base_mod.BaseGirlsTask.dump_unmark)
    patched.append("dump_unmark")
    TrainingTask.HANDLERS = handlers_mod.HANDLERS
    return patched, [h.__name__ for h in handlers_mod.HANDLERS]


class Task(BaseTask):

    def reset_to_start(self):
        """**故意什么都不做**：要从用户当前停着的那一页接手。"""
        return True

    def verify_start_state(self):
        return True

    def run(self):
        steps = _steps()
        patched, names = [], [h.__name__ for h in TrainingTask.HANDLERS]
        if os.path.exists(HOT_FLAG):
            patched, names = _hot_reload()
            self.log_info(f"hot_reload：已注入 {patched}")
        # 跑之前在日志里写清「这一份代码到底有没有加载到新 handler」——
        # reload_tasks 只重载任务类，深层的 src.* 子模块可能还是旧的（踩过）。
        self.log_info(f"接手 tick：{steps} 拍；HANDLERS={len(names)}；"
                      f"wheel={'handle_spin_wheel' in names}；"
                      f"unmarked={'handle_unmarked_cards' in names}；"
                      f"cook={'handle_ingredient_select' in names}")
        task, result = run_registered(self, TrainingTask,
                                      {"单局最大操作数": steps})
        report = {
            "steps": task._steps,
            "last_handler": task._last_state,
            "style": task.style,
            "style_known": task.style_known,
            "in_lobby": task.is_lobby(),
            "run_returned": result,
            "cook_wheel_registered": "handle_spin_wheel" in names,
            "hot_reloaded": bool(patched),
            "attempt_dir": task.attempt_dir,
        }
        self.log_info(f"接手结果：{report}")
        return report
