"""MCP 入口：报告当前 runtime 的权限/身份（排查「为什么杀不掉自己」）。"""

import ctypes
import os
import sys

from ok import BaseTask


class Task(BaseTask):

    def reset_to_start(self):
        return True

    def verify_start_state(self):
        return True

    def run(self):
        try:
            admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception as exc:                                # noqa: BLE001
            admin = f"err:{exc}"
        info = {
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "is_admin": admin,
            "python": sys.executable,
            "cwd": os.getcwd(),
            "user": os.environ.get("USERNAME"),
            "session": os.environ.get("SESSIONNAME"),
        }
        self.log_info(f"身份探测：{info}")
        return info
