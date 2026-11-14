"""后台监控：每 N 秒扫一次新增日志，只在**发现异常**时输出。

和 `tools/watch_pages.py --follow` 的区别：
那个每 10 秒把整份报告刷一遍（噪音大）；这个只在
「页面分类冲突 / 路由抢错 / 异常关键词 / 长时间无命中」时出声，
适合挂在后台盯一整局。

用法：
    python tools/watch_alerts.py           # 每 20 秒扫一次
    python tools/watch_alerts.py --interval 10
"""

import io
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "run.log")

TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d{3}")
PAGE = re.compile(r"页面分类：(\S+) → (\S+?)（候选 \[([^\]]*)\]）")
HIT = re.compile(r"命中 (handle_\w+)（第 (\d+) 步）")
BAD = re.compile(r"(异常|停止任务|硬卡|noresp|未能识别|死循环|推不动|接管)")

# 页面 → 期望的 handler（和 src/tasks/training/page_handlers.py 同源）
EXPECT = {
    "announcement": "handle_announcement", "captain": "handle_captain",
    "arcade": "handle_arcade", "boost_training": "handle_boost_training",
    "confirm_team": "handle_confirm_team", "construction": "handle_construction",
    "equipment": "handle_equipment", "event_choice": "handle_event_choice",
    "game_title": "handle_game_title", "go_out": "handle_go_out",
    "izumi_question": "handle_izumi_question", "lobby": "handle_lobby",
    "manuscript_submit": "handle_manuscript_submit",
    "note_study": "handle_note_study", "prepare": "handle_prepare",
    "rest_confirm": "handle_rest_confirm", "shop": "handle_shop",
    "skill_select": "handle_skill_select", "spin_wheel": "handle_spin_wheel",
    "stamina_recover": "handle_stamina_recover",
    "stamina_short": "handle_stamina_short", "stock_pick": "handle_stock_pick",
    "street": "handle_street", "team": "handle_team",
    "theme_select": "handle_theme_select",
    "training_entry": "handle_training_entry",
    "training_plan": "handle_training_plan",
    "training_score": "handle_training_score",
    "weekend_map": "handle_weekend", "vacation": "handle_vacation",
}


def read_lines():
    if not os.path.exists(LOG):
        return []
    return io.open(LOG, encoding="utf-8", errors="replace").read().splitlines()


def main():
    interval = 20
    if "--interval" in sys.argv:
        interval = int(sys.argv[sys.argv.index("--interval") + 1])
    seen = len(read_lines())
    print("监控启动：从第 %d 行开始，每 %d 秒扫一次" % (seen, interval), flush=True)
    pages = 0
    unknown = 0
    while True:
        lines = read_lines()
        if len(lines) < seen:
            print("[轮转] 日志变短，从头开始", flush=True)
            seen = 0
        new = lines[seen:]
        if new:
            seen = len(lines)
            cur_page = None
            for line in new:
                m = PAGE.search(line)
                if m:
                    pages += 1
                    cur_page = None if m.group(2) == "unknown" else m.group(2)
                    if cur_page is None:
                        unknown += 1
                    continue
                m = HIT.search(line)
                if m:
                    if cur_page is not None:
                        want = EXPECT.get(cur_page)
                        if want and m.group(1) != want:
                            print("[路由] 分类=%s 期望=%s 实际=%s  %s"
                                  % (cur_page, want, m.group(1),
                                     line[:23]), flush=True)
                    cur_page = None
                    continue
                m = BAD.search(line)
                if m:
                    print("[红灯] %s  %s" % (m.group(1), line[:170]), flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
