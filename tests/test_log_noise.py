"""`src/util/log_noise.py`：只丢 ok 那行刷屏 DEBUG，别的日志一律不动。

为什么要有这些用例：
    这条过滤器是「接线」型修复——不生效时不会报错，只会表现为控制台继续刷屏，
    而且接线点（`src/config.py`）很容易在某次回滚/重构里被顺手删掉。
    2026-11-10 第一次接线就是被另一会话的 `git reset --hard` 冲掉的，
    模块还在、接线没了，没人发现。这里把「接线在」和「只丢这一行」都锁住。

运行：
    .venv\\Scripts\\python.exe -m unittest tests.test_log_noise -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import logging  # noqa: E402

import src.config  # noqa: E402,F401  —— 导入即接线，用来验证接线没被删
from ok.util.logger import Logger  # noqa: E402
from src.util.log_noise import _DropNoisyDebug, install_quiet_filters  # noqa: E402

# 导入 src.config 那一刻是否已经在 "ok" logger 上装了过滤器
WIRED_AT_CONFIG_IMPORT = any(
    isinstance(f, _DropNoisyDebug) for f in logging.getLogger("ok").filters
)

NOISE = "get_task_by_class <class 'src.tasks.TrainingTask'> [<...>] [<...>]"


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class LogNoiseTest(unittest.TestCase):
    def setUp(self):
        self.ok_logger = logging.getLogger("ok")
        self.capture = _Capture()
        self.ok_logger.addHandler(self.capture)
        install_quiet_filters()  # 幂等，用例单独跑也成立

    def tearDown(self):
        self.ok_logger.removeHandler(self.capture)

    def test_wired_at_config_import(self):
        self.assertTrue(
            WIRED_AT_CONFIG_IMPORT,
            "src/config.py 导入时没有安装日志过滤器 —— 接线被删了？",
        )

    def test_noisy_debug_dropped(self):
        Logger("TaskExecutor").debug(NOISE)
        self.assertEqual(self.capture.messages, [])

    def test_other_debug_kept(self):
        Logger("TaskExecutor").debug("keep me")
        self.assertEqual(self.capture.messages, ["TaskExecutor:keep me"])

    def test_same_text_from_other_logger_kept(self):
        # 匹配的是「类名:方法名」整段前缀，别的类同名方法不算噪声
        Logger("OtherTask").debug("get_task_by_class something")
        self.assertEqual(
            self.capture.messages, ["OtherTask:get_task_by_class something"]
        )

    def test_higher_levels_kept(self):
        Logger("TaskExecutor").info(NOISE)
        Logger("TaskExecutor").warning(NOISE)
        self.assertEqual(len(self.capture.messages), 2)

    def test_install_is_idempotent(self):
        install_quiet_filters()
        install_quiet_filters()
        filters = [
            f for f in self.ok_logger.filters if isinstance(f, _DropNoisyDebug)
        ]
        self.assertEqual(len(filters), 1)


if __name__ == "__main__":
    unittest.main()
