"""把 logs/clicks.jsonl 转成「HH:MM:ss [事件]/点击[目标]」时间轴。

为什么存在：
    用户要求的一局记录格式是 `HH:MM:ss [事件]/点击[xx]`，
    clicks.jsonl 里已经有时间/命中 handler/点击文字/坐标，
    但 handler 名是英文，需要映射成人能读的场景名。

用法：
    python tools/run_timeline.py                      # 读 logs/clicks.jsonl
    python tools/run_timeline.py --src logs/xxx.jsonl --out logs/timeline.md
    python tools/run_timeline.py --dedupe 3           # 连续同一事件最多保留 3 行

输出：
    stdout 打印；`--out` 时同时写文件。每行：
    00:00:40 [训练日]/点击[训练@1199,654]
    坐标是客户区像素（1349x759 坐标系）。
"""

import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# handler 名 → 人能读的场景名。没列出的原样输出（去掉 handle_ 前缀）。
EVENT_NAMES = {
    "": "启动",
    "unknown": "未识别界面",
    "handle_training_day": "训练日",
    "handle_subject_select": "科目选择",
    "handle_boost_training": "强化训练二选一",
    "handle_training_result": "训练结果",
    "handle_training_hub": "训练中心",
    "handle_training_entry": "训练入口",
    "handle_story_arrow": "剧情",
    "handle_event_choice": "事件选择",
    "handle_dialog_continue": "对话继续",
    "handle_weekend": "周末",
    "handle_shop": "商店",
    "handle_street": "步行街",
    "handle_arcade": "游戏厅",
    "handle_park": "公园",
    "handle_restaurant": "餐厅",
    "handle_go_out": "出行",
    "handle_activity_go": "活动前往",
    "handle_vacation": "休假",
    "handle_daily_shop": "进店判断",
    "handle_rest_confirm": "休息确认",
    "handle_popup": "弹窗",
    "handle_draw": "抽奖",
    "handle_prepare": "赛前准备",
    "handle_in_match": "比赛中",
    "handle_match_intro": "比赛转场",
    "handle_match_result": "比赛结算",
    "handle_skill_select": "技能选择",
    "handle_result": "结算",
    "handle_lobby": "大厅",
    "handle_captain": "选队长",
    "handle_team": "选队友",
    "handle_equipment": "选器材",
    "handle_confirm_team": "确认编队",
    "handle_resume_training": "继续训练确认",
    "handle_club_members": "社团成员",
}


def event_name(state):
    if state in EVENT_NAMES:
        return EVENT_NAMES[state]
    return (state or "未知").replace("handle_", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(ROOT, "logs", "clicks.jsonl"))
    ap.add_argument("--out", default="")
    ap.add_argument("--dedupe", type=int, default=0,
                    help="连续同一事件最多保留 N 行（0=全部保留）")
    args = ap.parse_args()

    rows = []
    with open(args.src, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(rec)

    out = []
    prev_evt = None
    run = 0
    for rec in rows:
        evt = event_name(rec.get("state"))
        name = (rec.get("name") or "").strip()
        target = f"{name}@{rec.get('x')},{rec.get('y')}" if name else f"@{rec.get('x')},{rec.get('y')}"
        if evt == prev_evt:
            run += 1
        else:
            prev_evt, run = evt, 1
        if args.dedupe and run > args.dedupe:
            continue
        ts = rec.get("iso", "")
        if "." in ts:
            ts = ts.split(".")[0]
        out.append(f"{ts} [{evt}]/点击[{target}]")

    text = "\n".join(out)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n-> {args.out} ({len(out)} 行)")


if __name__ == "__main__":
    main()
