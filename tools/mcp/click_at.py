"""MCP 入口：急救点击 —— 不做 reset/verify，只按 logs/mcp/click_at.txt 点一下。

用于「回大厅被拒（当前在比赛/小游戏界面）」这种卡死现场：脚本必须跑在管理员
权限的 runtime 里才能把点击送进游戏（UIPI），而普通 shell 发的鼠标事件会被拦。

用法：
    1. 把坐标写进 logs/mcp/click_at.txt，格式 "x,y"（客户端坐标，1549x871 基准）；
    2. python tools/mcp/client.py tools/mcp/click_at.py
"""

import os

from ok import BaseTask

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POINT_FILE = os.path.join(ROOT, "logs", "mcp", "click_at.txt")


class Task(BaseTask):

    def reset_to_start(self):
        return True

    def verify_start_state(self):
        return True

    def run(self):
        with open(POINT_FILE, encoding="utf-8") as f:
            raw = f.read().strip()
        x_text, y_text = raw.split(",")
        x, y = int(x_text), int(y_text)
        self.click(x, y, after_sleep=1.0)
        self.log_info(f"急救点击 ({x}, {y})")
        return {"clicked": [x, y]}
