"""MCP 入口：让 runtime **立刻结束自己**（改了 helper 需要换新进程，但旧进程杀不掉时用）。

为什么需要：
    runtime 若是「管理员」起的，普通 shell 的 `Stop-Process` / `taskkill` 会被 UAC 拦；
    `exit_process` 在任务异常收尾时也可能只是排队、不真正退出。
    这个入口直接 `os._exit()`，进程立刻消失，随后由外面重新起一个（今天实测游戏本身
    不是管理员权限，非管理员 runtime 也能把点击送进窗口）。

用法：
    python tools/mcp/client.py tools/mcp/kill_self.py
    # 客户端会报连接错误 / 超时，那是预期的：服务端把自己杀了。
"""

import os

from ok import BaseTask


class Task(BaseTask):

    def reset_to_start(self):
        return True

    def verify_start_state(self):
        return True

    def run(self):
        pid = os.getpid()
        self.log_info(f"自杀退出 runtime pid={pid}")
        try:
            os.kill(pid, 9)          # Windows: TerminateProcess
        except Exception:            # noqa: BLE001
            pass
        os._exit(0)
        return {"pid": pid}
