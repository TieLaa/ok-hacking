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
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.abs_mouse import find_game_hwnd  # noqa: E402
from tools.collect_ssr import Screen, joined  # noqa: E402

POSITIONS = ("控卫", "分卫", "中锋", "大前", "小前")
STEP = 0.20          # 每次滚动 ~20% 屏高（实测一行卡约 0.29，留重叠避免跳过）
MAX_SCROLLS = 14


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


def parse_detail(boxes, w, h):
    """角色详情页：名字/稀有度/位置/流派/六维。"""
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
            "stats": stats, "tag": tag}


def section_of(headers):
    """当前屏的分区标题（只保留第一个「…角色一览」）。"""
    for h in headers:
        if "角色一览" in h:
            return h
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rarity", default="SR", choices=("SSR", "SR", "R"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-minutes", type=float, default=8.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out_path = args.out or os.path.join(
        ROOT, "docs", "roster", f"{args.rarity.lower()}_roster.json")

    hwnd = find_game_hwnd()
    if not hwnd:
        print("找不到游戏窗口")
        return 1
    screen = Screen(hwnd)
    deadline = time.time() + args.max_minutes * 60
    seen, clicked, rows = set(), set(), []
    target = f"{args.rarity}角色一览"
    try:
        boxes, w, h = read(screen)
        print("当前分区:", section_headers(boxes))
        if args.dry_run:
            for c in cards_in_view(boxes, w, h):
                print("  卡:", c)
            return 0

        print("先滚到顶…")
        scroll_to_top(screen, w, h)

        current = None
        passed_target = False
        no_move = 0
        scrolls = 0
        guard = 0
        while guard < 300 and scrolls <= MAX_SCROLLS and time.time() < deadline:
            guard += 1
            boxes, w, h = read(screen)
            t = joined(boxes)

            # 1) 详情页：解析 + 返回（列表会重置滚动位置，所以返回后重新读屏）
            if "升星" in t and "天赋" in t:
                info = parse_detail(boxes, w, h)
                if info["rarity"] != args.rarity:
                    print(f"    （{info['rarity']} {info['name']}，不是 {args.rarity}）")
                elif info["name"] in seen or info["name"] == "?":
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
                continue

            # 2) 分区判定：只有标题**以目标开头**才算（"SSR…".startswith("SR…") 为 False）
            head = section_of(section_headers(boxes))
            if head:
                current = head
                if current.startswith(args.rarity):
                    passed_target = True
                elif passed_target:
                    print(f"已经越过 {target}（到 {current}），收工")
                    break

            if not (current and current.startswith(args.rarity)):
                scrolls += 1
                if not scroll(screen, w, h, +1):
                    no_move += 1
                    if no_move >= 2:
                        print("连续两次没动 → 到底，且没找到目标分区")
                        break
                else:
                    no_move = 0
                continue

            # 3) 目标分区：找一张没点过的卡，按几何点（标签中心 + 0.13 屏高）
            cards = cards_in_view(boxes, w, h)
            slot = None
            for card in cards:
                key = (current, card["pos"], round(card["cx"], 2))
                if key not in clicked:
                    slot = (key, card)
                    break
            if slot is None:
                scrolls += 1
                if not scroll(screen, w, h, +1):
                    no_move += 1
                    print(f"  滚动没动（{no_move}/2），本区已采完")
                    if no_move >= 2:
                        break
                else:
                    no_move = 0
                continue

            key, card = slot
            clicked.add(key)
            for dy in (0.13, 0.17):
                screen.click(w * card["cx"], h * (card["cy"] + dy), after=0.8)
                if "升星" in joined(read(screen)[0]):
                    break
            else:
                print(f"    ！{key[1]} 点不开（{key[2]}）")
    finally:
        screen.close()

    print(f"\n采到 {len(rows)} 个：")
    for r in rows:
        print("  ", r)

    if not rows:
        return 1
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    merged, order = {}, []
    if os.path.exists(out_path):
        try:
            for r in json.load(open(out_path, encoding="utf-8")):
                if r.get("name") and r["name"] not in merged:
                    merged[r["name"]] = r
                    order.append(r["name"])
        except Exception as e:                       # noqa: BLE001
            print("旧文件读不出来，直接覆盖：", e)
    for r in rows:
        if r.get("name") and r["name"] not in merged:
            order.append(r["name"])
        merged[r.get("name")] = r
    out = [merged[n] for n in order if n]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"已写入 {out_path}（本次 {len(rows)} 个，累计 {len(out)} 个）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

