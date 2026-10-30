"""PostMessage 输入探测：验证 ok-script 2.0.20 的 PostMessage 修复对本游戏是否生效。

背景（见 docs/RULES.md / 记忆）：
- ok-script 2.0.0b7 时代，PostMessage 系（含 WM_ACTIVATE、同步光标）对本游戏
  **全部无效**，只有真实 mouse_event / keybd_event 有效，所以
  `src/config.py` 的 windows.interaction 只保留 GirlsInteraction。
- 2.0.20 修了 PostMessage 鼠标坐标（`update_mouse_pos` 按子窗口选 target、
  按钮消息复用同一套 client 坐标）。本探测只回答一个问题：
  **现在用 PostMessage 点「赛事」，游戏会不会进入玩法目录？**

安全设计：
- 只点「赛事」（打开玩法目录，不消耗任何游戏资源）；
- 点击用**独立的** PostMessageInteraction 实例，不动全局 interaction；
- **默认不碰真实鼠标**：对照实验（同一坐标用 GirlsInteraction 真点一次）会抢前台，
  只有显式设置 ``OK_PM_CONTROL=1`` 才会执行——它会短暂夺走你的光标，别在工作时开；
- 无论成败，最后都用全局 GirlsInteraction 走 recover_to_lobby 回大厅；
- 前后帧与 OCR 都存进本次运行目录（frames/），成功/失败都有证据。

跑法（管理员终端 + 游戏窗口可见）：

    python tools/mcp/client.py tools/mcp/postmessage_probe.py

结果里 `postmessage_worked` 就是结论；`diff_texts` 是前后 OCR 差异，
能看出到底是「点开了玩法目录」还是「画面根本没变」。
"""

import os

from ok import BaseTask, og

from src.tasks.TrainingTask import TrainingTask

from _harness import borrow

# 点这个按钮进入「玩法目录」：无资源消耗，且页面标题唯一
TARGET_TEXT = "赛事"
EXPECT_TEXTS = ("玩法目录", "赛程", "比赛")


class Task(BaseTask):

    def reset_to_start(self):
        # OK_PM_NO_RESET=1：只读探测，绝不动真实鼠标（不点返回、不抢前台）。
        # 用途：你在用电脑时想跑一次 PostMessage 验证，又不想被打断。
        if os.environ.get("OK_PM_NO_RESET") == "1":
            with borrow(self, TrainingTask, self.artifact_directory) as task:
                return task.next_frame() is not None
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            return task.reset_to_start()

    def verify_start_state(self):
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            return task.verify_start_state()

    def run(self):
        from ok.device.interaction_methods.post_message import PostMessageInteraction

        with borrow(self, TrainingTask, self.artifact_directory) as task:
            before = task._ocr_region()
            assert before, "探测失败：这一帧没有识别到任何文字"
            assert task.is_lobby(before), "探测失败：不在大厅，先人工回到大厅"
            target = task.match_text(before, TARGET_TEXT)
            assert target is not None, f"探测失败：大厅里没找到「{TARGET_TEXT}」"

            x = int(target.x + target.width / 2)
            y = int(target.y + target.height / 2)
            before_names = {t.name.strip() for t in before}
            task.attempt_dump("pm_before", before)

            device = og.device_manager
            hwnds = getattr(device.hwnd_window, "hwnds", []) or []
            pm = PostMessageInteraction(device.capture_method, device.hwnd_window)
            task.log_info(f"用 PostMessage 点「{TARGET_TEXT}」@({x},{y}) hwnd={pm.hwnd} "
                          f"子窗口={len(hwnds)}")
            pm.click(x, y)
            task.sleep(1.5)

            after = task._ocr_region()
            task.attempt_dump("pm_after", after)
            after_names = {t.name.strip() for t in after}
            diff = sorted(after_names - before_names)
            appeared = [name for name in EXPECT_TEXTS if name in after_names]
            worked = bool(appeared)

            # 对照实验（默认关闭）：同一个目标、同一坐标，用真实鼠标再点一次。
            # **会抢前台、夺走光标**，只在 OK_PM_CONTROL=1 时执行。
            # 用途：PostMessage 无效时区分「消息被游戏忽略」和「坐标点错了」。
            pm_target = getattr(pm, "_dynamic_target_hwnd", 0)
            pm_pos = getattr(pm, "mouse_pos", None)
            real_worked = None
            real_diff = []
            if not worked and os.environ.get("OK_PM_CONTROL") == "1":
                task.log_info("PostMessage 没生效 → 用真实鼠标点同一位置做对照"
                              "（会抢前台，OK_PM_CONTROL=1 才会跑）")
                task.click(x, y, after_sleep=1.5)
                real_after = task._ocr_region()
                task.attempt_dump("real_after", real_after)
                real_names = {t.name.strip() for t in real_after}
                real_appeared = [name for name in EXPECT_TEXTS if name in real_names]
                real_worked = bool(real_appeared)
                real_diff = sorted(real_names - after_names)

            report = {
                "postmessage_worked": worked,
                "target": TARGET_TEXT,
                "click_xy": [x, y],
                "hwnd": pm.hwnd,
                "child_hwnds": len(hwnds),
                "pm_target_hwnd": pm_target,
                "pm_mouse_pos": list(pm_pos) if pm_pos else None,
                "appeared": appeared,
                "diff_texts": diff[:15],
                "real_click_worked": real_worked,
                "real_diff_texts": real_diff[:15],
                "after_count": len(after),
                "attempt_dir": task.attempt_dir,
            }
            task.log_info("PostMessage 探测结果：" + str(report), notify=True)

            # 只在真的点开过页面时才需要恢复；OK_PM_NO_RESET 模式下全程不碰真实鼠标
            if os.environ.get("OK_PM_NO_RESET") == "1":
                report["restored_to_lobby"] = task.is_lobby(after)
            else:
                restored = task.recover_to_lobby()
                report["restored_to_lobby"] = restored
                if not restored:
                    task.attempt_dump("pm_restore_failed")
            return report
