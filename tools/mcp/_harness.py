"""MCP 薄 harness 的公共部分（不是任务，不会被 GUI 注册）。

为什么需要它：MCP 的 ``run_task`` 提交的是**文件**，文件里必须有
``class Task(BaseTask)`` 并实现 ``reset_to_start`` / ``verify_start_state`` / ``run``。
业务逻辑不能复制到 harness 里，所以 harness 只做两件事：

1. 通过 ``get_task_by_class`` 拿到**已注册的真实任务实例**；
2. 借出它的执行上下文（``_enabled`` / ``running`` / ``ArtifactDirectory`` /
   ``executor.current_task``），跑完在 finally 里原样还回去。

注意：借上下文这项操作有副作用，只允许在 MCP 单次运行里做；
GUI 正常启动任务时由执行器自己设置这些字段，不需要 harness。
"""

import time


class BorrowedTask:
    """上下文管理器：临时让已注册任务实例「像被调度器启动一样」可执行。"""

    def __init__(self, harness, task, artifact_directory=None):
        self.harness = harness
        self.task = task
        self.artifact_directory = artifact_directory
        self._saved = None

    def __enter__(self):
        task = self.task
        executor = self.harness.executor
        self._saved = {
            "enabled": getattr(task, "_enabled", None),
            "running": getattr(task, "running", None),
            "start_time": getattr(task, "start_time", None),
            "artifact_directory": getattr(task, "artifact_directory", None),
            "current_task": getattr(executor, "current_task", None),
        }
        task._enabled = True
        task.running = True
        task.start_time = time.time()
        if self.artifact_directory:
            task.artifact_directory = str(self.artifact_directory)
        executor.current_task = task
        return task

    def __exit__(self, exc_type, exc, tb):
        task = self.task
        executor = self.harness.executor
        saved = self._saved or {}
        task._enabled = saved.get("enabled")
        task.running = saved.get("running")
        task.start_time = saved.get("start_time")
        previous = saved.get("artifact_directory")
        if previous is None:
            if hasattr(task, "artifact_directory"):
                delattr(task, "artifact_directory")
        else:
            task.artifact_directory = previous
        executor.current_task = saved.get("current_task")
        # 清掉本次运行的产物计数（任务实例是复用的）
        on_destroy = getattr(task, "on_destroy", None)
        if callable(on_destroy):
            try:
                on_destroy()
            except Exception:                    # noqa: BLE001
                pass
        return False


def borrow(harness, cls, artifact_directory=None):
    """按类名借出已注册任务实例，返回上下文管理器。"""
    task = harness.get_task_by_class(cls)
    if task is None:
        raise RuntimeError(
            f"{cls.__name__} 没有注册：检查 src/config.py 的 onetime_tasks/trigger_tasks")
    return BorrowedTask(harness, task, artifact_directory)


def run_registered(harness, cls, config_overrides=None):
    """借已注册任务跑一次，返回 (task, run_result)。

    - 临时覆盖 config（跑完还原），用于「限制步数/关闭危险开关」这类本次运行参数；
    - 键名以 `_` 开头 → 设置**任务实例属性**（如 `_launch_queue` 队列参数），
      不进 config、不落盘，跑完还原原值；
    - 业务逻辑全在任务类里，这里不复制任何判据。
    """
    with borrow(harness, cls, harness.artifact_directory) as task:
        saved = {}
        for key, value in (config_overrides or {}).items():
            if key.startswith("_"):
                saved[key] = getattr(task, key, None)
                setattr(task, key, value)
            else:
                saved[key] = task.config.get(key)
                task.config[key] = value
        try:
            result = task.run()
        finally:
            for key, value in saved.items():
                if key.startswith("_"):
                    if value is None:
                        # 原来没有这个属性就删掉，别留 None 干扰 getattr 判断
                        try:
                            delattr(task, key)
                        except AttributeError:
                            pass
                    else:
                        setattr(task, key, value)
                else:
                    task.config[key] = value
        return task, result
