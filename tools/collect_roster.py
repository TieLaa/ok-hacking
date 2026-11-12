"""采集卡池详情里所有角色（SSR / SR / R 分区在一页上，靠滚动浏览）。

和 collect_ssr.py 的区别（那个脚本的滚动写错了，2026-10-30 用户指出）：
- 不再「固定两下大拖动」：一次拖 0.42 屏高会直接越过整个 SR 区，落到最底部 R 区；
- 现在**先滚到顶**，再每次只拖 ~0.2 屏高，并用「首张卡的位置变化」验证是否真的动了；
- 一屏有几张卡，由「位置标签」（控卫/分卫/中锋/大前/小前）决定，不再把筛选按钮当卡；
- 连续两次滚动没动 = 到底；另有 --max-minutes 总超时，不会卡住。

用法：
    python tools/collect_roster.py --rarity SR --out docs/roster/sr_roster.json
    python tools/collect_roster.py --rarity SR --dry-run     # 只打印看见的分区和卡，不点
"""

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.abs_mouse import find_game_hwnd  # noqa: E402
from tools.collect_ssr import Screen, joined, preferred_of  # noqa: E402
from tools._roster_json import merge_and_save  # noqa: E402

POSITIONS = ("控卫", "分卫", "中锋", "大前", "小前")
STEP = 0.20          # （旧参数，仅 scroll() 的默认步长，采集改用 ROW_DY）
MAX_SCROLLS = 14

# ---- 实测几何（2026-10-30 量出来的，一次性标定脚本已删，数值以这里为准）----
# 5 列：cx = 0.228 / 0.366 / 0.505 / 0.643 / 0.783（列间距 0.139 屏宽 = 216px）
COL_X = (0.228, 0.366, 0.505, 0.643, 0.783)
COL_DX = 0.139
CARD_H = 0.245       # 卡片高（轮廓检测：y 518~744 / 871）
ROW_DY = 0.300       # 行距（同屏两行 0.428 与 0.728 的差）
CLICK_DY = 0.095     # 点击点 = 位置标签中心 + 0.095 屏高（卡片视觉中心）
ROWS_SSR = 4         # 18 张 / 5
ROWS_SR = 2          # 10 张 / 5
ROWS_R = 1           # 3 张


def read(screen):
    boxes, w, h = screen.boxes()
    return boxes, w, h


def section_headers(boxes):
    return [b["text"] for b in boxes if "之一览" in b["text"] or "角色一览" in b["text"]]


def cards_in_view(boxes, w, h):
    """当前屏的卡片：每张卡顶部有一个位置标签。返回 [{pos, cx, cy}]。"""
    out = []
    for b in boxes:
        if b["text"] not in POSITIONS:
            continue
        cx = (b["x"] + b["w"] / 2) / w
        cy = (b["y"] + b["h"] / 2) / h
        if not (0.10 <= cx <= 0.90 and 0.25 <= cy <= 0.90):
            continue
        out.append({"pos": b["text"], "cx": cx, "cy": cy})
    out.sort(key=lambda c: (round(c["cx"] * 8), c["cy"]))
    return out


def scroll(screen, w, h, direction):
    """滚一格。direction=+1 往下看更后面的角色，-1 滚回顶部。

    返回 False 表示没动（到底/到顶）。
    """
    boxes0, _, _ = read(screen)
    before = cards_in_view(boxes0, w, h)
    y_before = before[0]["cy"] if before else None

    if direction > 0:
        y1, y2 = h * 0.78, h * (0.78 - STEP)
    else:
        y1, y2 = h * 0.30, h * (0.30 + STEP)
    screen.swipe(w * 0.5, y1, w * 0.5, y2, steps=28, hold=0.02)

    boxes1, _, _ = read(screen)
    after = cards_in_view(boxes1, w, h)
    y_after = after[0]["cy"] if after else None
    if y_before is None or y_after is None:
        return joined(boxes1) != joined(boxes0)
    return abs(y_after - y_before) > 0.01


def scroll_to_top(screen, w, h, limit=8):
    for i in range(limit):
        if not scroll(screen, w, h, -1):
            print(f"  已到顶（滚了 {i} 次）")
            return
    print(f"  ！滚了 {limit} 次还没到顶，继续")


def parse_detail(boxes, frame, w, h):
    """角色详情页：名字/稀有度/位置/流派/六维/推荐属性（黄色大拇指）。"""
    import re
    name_box = None
    rarity = "?"
    for b in boxes:
        m = re.match(r"^(SSR|SR|R)(.+)$", b["text"])
        if m and len(m.group(2).strip()) >= 2:
            rarity, name_box = m.group(1), b
            break
    if name_box is None:
        # 名字被拆成两个框：左边只有「SR」，真名在右边同一行
        tag_box = next((b for b in boxes if b["text"].strip() in ("SSR", "SR", "R")), None)
        if tag_box is not None:
            rarity = tag_box["text"].strip()
            cy = tag_box["y"] + tag_box["h"] / 2
            right = [b for b in boxes
                     if b["x"] > tag_box["x"] + tag_box["w"] - 5
                     and abs((b["y"] + b["h"] / 2) - cy) < tag_box["h"] * 0.9
                     and 2 <= len(b["text"].strip()) <= 8]
            right.sort(key=lambda b: b["x"])
            if right:
                name_box = right[0]
                name_box["text"] = f"{rarity}{right[0]['text'].strip()}"
    name = "?"
    if name_box is not None:
        name = re.sub(r"^(SSR|SR|R)", "", name_box["text"]).strip()
        name = name.lstrip("-_·•.。 　")          # OCR 噪声前缀
    if name in ("?", "") or len(name) < 2:
        # 兜底：详情页整段文字是 "…OFFSR亚理纱贾斯敏详情…"，直接从拼接文本里切
        plain = "".join(b["text"] for b in boxes)
        m = re.search(r"(SSR|SR|R)([^详情满级O]{2,10}?)详情", plain)
        if m:
            rarity, name = m.group(1), m.group(2).strip().lstrip("-_·•.。 　")

    pos = next((b["text"] for b in boxes if b["text"] in POSITIONS), "?")
    # 流派：右下角以「流」结尾的短词（小游戏流 / 商店流 / 训练流 / 扭蛋流）
    style = "?"
    for b in boxes:
        t = b["text"].strip()
        cx = (b["x"] + b["w"] / 2) / w
        cy = (b["y"] + b["h"] / 2) / h
        if t.endswith("流") and 2 <= len(t) <= 4 and cx > 0.55 and cy > 0.55:
            style = t
            break
    # 六维：右侧一列 25/33/57/49/27/39 这样的 2~3 位数
    nums = []
    for b in boxes:
        t = b["text"].strip()
        if t.isdigit() and len(t) <= 3 and b["x"] > w * 0.70:
            nums.append((b["y"], int(t)))
    nums.sort()
    stats = [v for _y, v in nums][:6]
    tag = next((b["text"] for b in boxes
                if 2 <= len(b["text"]) <= 5 and b["text"] not in POSITIONS
                and not b["text"].isdigit() and b["x"] > w * 0.55
                and "流" not in b["text"]), None)
    return {"name": name, "rarity": rarity, "position": pos, "style": style,
            "stats": stats, "tag": tag,
            "preferred": preferred_of(frame, w, h, boxes) if frame is not None else []}


def section_of(headers):
    """当前屏的分区标题（只保留第一个「…角色一览」）。"""
    for h in headers:
        if "角色一览" in h:
            return h
    return None


def drag_rows(screen, w, h, rows=1):
    """按实测行距 0.30 屏高精确滚动 rows 行（不做「拖动后猜测」）。"""
    screen.swipe(w * 0.5, h * 0.80, w * 0.5, h * (0.80 - ROW_DY * rows),
                 steps=30, hold=0.02)


def goto_row(screen, w, h, row_offset):
    """回到顶部，再精确下滚 row_offset 行（详情页返回后列表会重置到顶，必须重定位）。"""
    scroll_to_top(screen, w, h, limit=16)
    for _ in range(row_offset):
        drag_rows(screen, w, h, 1)


def find_section_row(screen, w, h, rarity, limit=8):
    """从顶部开始逐行下滚，返回目标分区首次出现时的行偏移。

    2026-11-12：SSR 分区的黄底横幅 OCR 常把「SSR」读成「R」。分区顺序固定
    SSR→SR→R，所以读到「R…」时，还没找过的分区里第一个就是它 —— 先按 SSR 认，
    SSR 采完再按 SR，最后才是 R。
    """
    order = ("SSR", "SR", "R")
    idx = order.index(rarity)
    scroll_to_top(screen, w, h, limit=16)
    for k in range(limit):
        boxes, _, _ = read(screen)
        head = section_of(section_headers(boxes))
        if head:
            word = head.replace(" ", "")
            for cand in order[idx:]:
                if word.startswith(cand) or word.startswith(cand.lower()):
                    return k
            if word.startswith("R") and idx < 2:
                return k
        drag_rows(screen, w, h, 1)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rarity", default="SR", choices=("SSR", "SR", "R"))
    ap.add_argument("--rows", type=int, default=None, help="该分区的行数（默认按实测）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-minutes", type=float, default=8.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out_path = args.out or os.path.join(
        ROOT, "docs", "roster", f"{args.rarity.lower()}_roster.json")
    rows_wanted = args.rows or {"SSR": ROWS_SSR, "SR": ROWS_SR, "R": ROWS_R}[args.rarity]

    hwnd = find_game_hwnd()
    if not hwnd:
        print("找不到游戏窗口")
        return 1
    screen = Screen(hwnd)
    deadline = time.time() + args.max_minutes * 60
    seen, rows = set(), []
    try:
        boxes, w, h = read(screen)
        if args.dry_run:
            print("分区:", section_headers(boxes))
            for c in cards_in_view(boxes, w, h):
                print("  卡:", c)
            return 0

        k = find_section_row(screen, w, h, args.rarity)
        if k is None:
            print(f"找不到 {args.rarity}角色一览 分区")
            return 1
        print(f"{args.rarity}角色一览 在上滚 {k} 行的位置；共 {rows_wanted} 行 x 5 列")

        for r in range(rows_wanted):
            goto_row(screen, w, h, k + r)
            boxes, w, h = read(screen)
            labels = cards_in_view(boxes, w, h)
            if not labels:
                print(f"  第 {r} 行没有标签，跳过")
                continue
            cy = max(c["cy"] for c in labels)      # 最下面那行 = 本行
            print(f"  第 {r} 行 label_cy={cy:.3f}")
            for col, cx in enumerate(COL_X):
                if time.time() > deadline:
                    print("  ！超时收工")
                    return _save(rows, out_path)
                # 每张卡点击前都重新定位（返回会重置列表）
                goto_row(screen, w, h, k + r)
                screen.click(w * cx, h * (cy + CLICK_DY), after=0.9)
                b2, w2, h2 = read(screen)
                if not ("升星" in joined(b2) and "天赋" in joined(b2)):
                    screen.click(w * cx, h * (cy + CLICK_DY + 0.02), after=0.9)
                    b2, w2, h2 = read(screen)
                if not ("升星" in joined(b2) and "天赋" in joined(b2)):
                    print(f"    ！第{r}行第{col+1}列 点不开")
                    continue
                info = parse_detail(b2, getattr(screen, "last_frame", None), w2, h2)
                if info["name"] in seen or info["name"] == "?":
                    print(f"    （{info['name']} 已采/未识别）")
                else:
                    seen.add(info["name"])
                    rows.append(info)
                    print(f"  [{len(rows)}] {info['rarity']} {info['name']}"
                          f"｜{info['position']}｜{info['style']}｜{info['stats']}")
                for _ in range(4):
                    screen.click(72, 58, after=0.8)
                    if "升星" not in joined(read(screen)[0]):
                        break
    finally:
        screen.close()
    return _save(rows, out_path)


def _save(rows, out_path):
    print(f"\n采到 {len(rows)} 个：")
    for r in rows:
        print("  ", r)
    if not rows:
        return 1
    merge_and_save(rows, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

