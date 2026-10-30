"""模板自带的演示用基类。

真正跑游戏的类在 ``BaseGirlsTask``；这里保留下来只是为了 ``MyOneTimeTask``
这类演示任务还能用。MCP 适配层同样接上，保证演示任务也能被 ``run_task`` 拉起。
"""

from ok import BaseTask

from src.tasks.attempt import AttemptSupport


class MyBaseTask(AttemptSupport, BaseTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
