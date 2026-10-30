"""MCP 入口：抓一帧存到 logs/debug/manual_shot2.jpg（管理员权限，能抓到真实画面）。

用法：
    python tools/mcp/client.py tools/mcp/shot.py
"""

import os

import cv2

from ok import BaseTask

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "logs", "debug", "manual_shot2.jpg")


class Task(BaseTask):

    def reset_to_start(self):
        return True

    def verify_start_state(self):
        return True

    def run(self):
        frame = self.frame
        if frame is None or not frame.any():
            self.log_warning("抓帧失败：frame 为空（窗口可能不可见）")
            return {"saved": None}
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        cv2.imwrite(OUT, frame)
        self.log_info(f"已存帧 {OUT} {frame.shape[1]}x{frame.shape[0]}")
        return {"saved": OUT, "size": [int(frame.shape[1]), int(frame.shape[0])]}
