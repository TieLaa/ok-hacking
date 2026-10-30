"""全量扫描卡池详情：从顶到底数清每个分区到底有几张卡（不点击，只读）。

方法：
- 每次只滚 0.05 屏高（约 1/6 行），相邻两屏大量重叠，靠**绝对坐标**去重
- 位移用「同一列标签 cy 的变化中位数」估计，累加成绝对 y
- 卡片身份 = (分区, 列cx, 绝对y)，同一张卡在不同屏里算出来一致
- 连续 4 步没有新卡 = 到头/到底

用法：python tools/scan_pool.py
"""

import sys

ROOT = __file__.rsplit("\\tools\\", 1)[0]
sys.path.insert(0, ROOT)

from tools.abs_mouse import find_game_hwnd  # noqa: E402
from tools.collect_roster import cards_in_view, read, section_of, section_headers  # noqa: E402
from tools.collect_ssr import Screen  # noqa: E402

STEP = 0.05
MAX_STEPS = 40


def scroll_step(screen, w, h, direction):
    """direction=-1 向上翻（看更前面），+1 向下翻（看更后面）。"""
    if direction < 0:
        y1, y2 = h * 0.35, h * (0.35 + STEP)
    else:
        y1, y2 = h * 0.75, h * (0.75 - STEP)
    screen.swipe(w * 0.5, y1, w * 0.5, y2, steps=20, hold=0.02)


def scan(screen, direction):
    records = {}
    section = None
    prev = {}
    offset = 0.0
    no_new = 0
    for _step in range(MAX_STEPS):
        boxes, w, h = read(screen)
        head = section_of(section_headers(boxes))
        if head:
            section = head
        cards = {round(c["cx"], 2): c["cy"] for c in cards_in_view(boxes, w, h)}

        if prev and cards:
            diffs = sorted(prev[cx] - cards[cx] for cx in cards if cx in prev)
            if diffs:
                offset += diffs[len(diffs) // 2]

        new = 0
        for cx, cy in cards.items():
            key = (section, cx, round((cy + offset) * 100))
            if key not in records:
                records[key] = {"cx": cx, "ay": round((cy + offset) * 100)}
                new += 1
        prev = cards
        no_new = no_new + 1 if new == 0 else 0
        if no_new >= 4:
            break
        scroll_step(screen, w, h, direction)
    return records


def main():
    hwnd = find_game_hwnd()
    if not hwnd:
        print("找不到游戏窗口")
        return 1
    screen = Screen(hwnd)
    try:
        print("向上扫到顶…")
        up = scan(screen, -1)
        print(f"  上半部分记录 {len(up)} 条")
        print("向下扫到底…")
        down = scan(screen, +1)
        print(f"  下半部分记录 {len(down)} 条")
    finally:
        screen.close()

    allrec = dict(up)
    allrec.update(down)
    by_section = {}
    for (section, cx, ay), v in allrec.items():
        by_section.setdefault(section, set()).add((cx, ay))
    total = 0
    for section, items in sorted(by_section.items(), key=lambda kv: str(kv[0])):
        cols = sorted({cx for cx, _ in items})
        rows = sorted({ay for _, ay in items})
        print(f"\n{section}: {len(items)} 张｜列 {cols}｜行数 {len(rows)}")
        total += len(items)
    print(f"\n合计 {total} 张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
