"""一次性解锁：从「编队/战术板」这类带比赛标记的**菜单页**安全退回大厅。

背景：`recover_to_lobby` 把「战术板」当比赛/小游戏标记直接放弃（红线是
**对局内**盲点返回会误触技能）。但「战术板 + 自动编队/保存调整/阵容评分」
是菜单页，没有对局 HUD（比分/暂停/VS），返回是安全的。

实测（2026-11-06 13:01）：这页 ESC 无效（连按 8 次画面不变），
退出按钮是**左上角的《**（OCR 框 (52,42,40,32)，docs/MCP_DEV.md 坑 1 同款）。

手段：按一次 ESC（万一有效省事）→ 没退就点《框；每步之间 OCR 复核，
出现真对局特征（暂停/回合/结算）立刻停手交人工，绝不点业务按钮。
"""

import json

from ok import BaseTask

from src.tasks.TrainingTask import TrainingTask

from _harness import borrow

# 菜单页特征：有这些词说明是编队/奖励浏览页，不是对局内
MENU_MARKS = ("战术板", "阵容评分", "自动编队", "保存调整", "奖励总览", "下级奖励")
# 对局内特征：这些词出现就真放弃，绝不点
REAL_MATCH_MARKS = ("暂停", "回合", "结算")


class Task(BaseTask):

    def reset_to_start(self):
        return True

    def verify_start_state(self):
        return True

    def run(self):
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            history = []
            for i in range(8):
                texts = task._ocr_region()
                names = [t.name.strip() for t in texts if t.name.strip()]
                if task.is_lobby(texts):
                    self.log_info(f"第 {i} 步后：已在大厅 ✓")
                    return {"ok": True, "steps": i, "history": history}
                joined = "|".join(names)
                real_match = any(m in joined for m in REAL_MATCH_MARKS)
                menu = any(m in joined for m in MENU_MARKS)
                if real_match and not menu:
                    self.log_error(f"疑似真对局界面（{joined[:80]}）→ 不碰，交人工")
                    task.attempt_dump("exit_refused_real_match", texts)
                    return {"ok": False, "reason": "real_match", "texts": names[:15]}
                history.append({"i": i, "head": names[:6]})
                if i == 0:
                    self.log_info("先按一次 ESC 试试")
                    task.send_key("esc")
                else:
                    # 找左上角的《/< 返回框；找不到就点固定角 (72,58)
                    box = next((t for t in texts
                                if t.name.strip() in ("《", "<", "〈", "×")
                                and t.x < 120 and t.y < 120), None)
                    if box is not None:
                        cx, cy = int(box.x + box.width / 2), int(box.y + box.height / 2)
                        self.log_info(f"点返回按钮「{box.name}」@({cx},{cy})")
                        task.click(cx, cy)
                    else:
                        self.log_info("没找到《框 → 点左上角固定坐标 (72,58)")
                        task.click(72, 58)
                task.sleep(1.4)
            texts = task._ocr_region()
            names = [t.name.strip() for t in texts if t.name.strip()]
            task.attempt_dump("exit_gave_up", texts)
            self.log_error(f"8 步没退回大厅，最后画面：{names[:10]}")
            return {"ok": False, "reason": "stuck", "texts": names[:15]}
