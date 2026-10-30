"""把「卡池详情」里所有角色卡都点一遍，按名字去重（不分分区、不靠标题）。

为什么这么写（2026-10-30 用户指出）：
- 这页有 4 个分区（SSR角色一览 / 角色一览 / SR角色一览 / R角色一览），
  靠标题找分区分错过一次（无前缀的「角色一览」整批漏掉），所以不再按标题筛；
- 位置标签只用来定位卡片，卡片身份一律用**详情页里的名字**；
- 实测：详情页返回后列表位置不变（同一屏标签坐标一致），所以一屏内的卡可以连续点；
- 滚动：一次 0.30 屏高的拖动，内容只移动约 0.095（实测），所以滚一行要拖 3 次。

用法：
    python tools/collect_all.py                  # 全部点一遍，按稀有度存三个 json
    python tools/collect_all.py --max-minutes 12
"""

import argparse
import json
import os
import re
import sys
import time

ROOT = __file__.rsplit("\\tools\\", 1)[0]
sys.path.insert(0, ROOT)

from tools.abs_mouse import find_game_hwnd  # noqa: E402
from tools.collect_roster import (COL_X, CLICK_DY, cards_in_view, drag_rows,  # noqa: E402
                                  parse_detail, read)
from tools.collect_ssr import Screen, joined  # noqa: E402

OUT_DIR = os.path.join(ROOT, "docs", "roster")


def click_and_read(screen, w, h, cx, cy):
    """点一张卡，返回详情信息（失败返回 None），并返回列表。

    点击点 = 标签中心 + 0.095 屏高（卡片视觉中心），但**必须夹在弹窗内**：
    第二行卡的标签在 0.82，加 0.095 就到 0.915，已经出了弹窗底边(~0.86)，
    之前那 5 张「点不开」全是这么白点的。
    """
    click_y = min(cy + CLICK_DY, 0.82)
    for dy in (click_y, click_y - 0.03):
        screen.click(w * cx, h * dy, after=0.9)
        boxes, w2, h2 = read(screen)
        if "升星" in joined(boxes) and "天赋" in joined(boxes):
            info = parse_detail(boxes, w2, h2)
            for _ in range(4):
                screen.click(72, 58, after=0.8)
                if "升星" not in joined(read(screen)[0]):
                    break
            return info
    return None


def card_signature(frame, w, h, cx, cy):
    """卡片立绘的感知指纹：不看名字也能判断「这张卡点过没有」。

    2026-10-30 用户点出的无效点击来源：每滚一屏就把**所有可见卡**重新点一遍，
    哪怕上一屏刚点过。卡片没有名字文字，但立绘是唯一的——16x16 灰度足够区分。
    """
    import cv2
    x1 = int((cx - 0.05) * w)
    x2 = int((cx + 0.05) * w)
    y1 = int((cy + 0.03) * h)
    y2 = int((cy + 0.22) * h)
    crop = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
    if crop.size == 0:
        return None
    small = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (16, 16),
                       interpolation=cv2.INTER_AREA)
    return small.tobytes()


def bring_row_into_view(screen, w, h, boxes):
    """把「半截露在弹窗外」的行往上挪，让整张卡都可见再点。

    2026-10-30 实探：第二行的位置标签在 cy≈0.82，卡身在标签**下方**，
    而弹窗底边约 0.86 —— 点标签下方必然落在空白处，表现为「点不开」。
    正确做法是先把这一行整体上移（内容下移），让标签落到 0.68 以上。
    """
    for _ in range(4):
        cards = cards_in_view(boxes, w, h)
        if not cards or max(c["cy"] for c in cards) <= 0.68:
            return boxes
        screen.swipe(w * 0.5, h * 0.40, w * 0.5, h * 0.50, steps=24, hold=0.02)
        boxes, _, _ = read(screen)
    return boxes


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=float, default=12.0)
    ap.add_argument("--max-steps", type=int, default=30)
    args = ap.parse_args(argv)

    hwnd = find_game_hwnd()
    if not hwnd:
        print("找不到游戏窗口")
        return 1
    screen = Screen(hwnd)
    found = {}
    seen_sigs = {}
    click_count = {"n": 0}
    _orig_click = screen.click

    def _counted_click(x, y, after=0.6):
        click_count["n"] += 1
        return _orig_click(x, y, after=after)

    screen.click = _counted_click
    deadline = time.time() + args.max_minutes * 60
    try:
        # 先滚到顶
        for _ in range(20):
            b, w, h = read(screen)
            before = [(c["pos"], round(c["cx"], 2), round(c["cy"], 2))
                      for c in cards_in_view(b, w, h)]
            screen.swipe(w * 0.5, h * 0.35, w * 0.5, h * 0.45, steps=20, hold=0.02)
            b2, _, _ = read(screen)
            after = [(c["pos"], round(c["cx"], 2), round(c["cy"], 2))
                     for c in cards_in_view(b2, w, h)]
            if before == after:
                break

        stagnant = 0
        for step in range(args.max_steps):
            if time.time() > deadline:
                print("！超时收工")
                break
            boxes, w, h = read(screen)
            boxes = bring_row_into_view(screen, w, h, boxes)
            frame = screen.frame()
            cards = cards_in_view(boxes, w, h)
            print(f"\n第 {step} 屏：{len(cards)} 张卡")
            new_here = 0
            skipped = 0
            for card in cards:
                if time.time() > deadline:
                    break
                sig = card_signature(frame, w, h, card["cx"], card["cy"])
                if sig is not None and sig in seen_sigs:
                    skipped += 1
                    continue          # 立绘指纹见过 → 不点，省掉无效点击
                info = click_and_read(screen, w, h, card["cx"], card["cy"])
                if info is None:
                    print(f"    ！{card['pos']} 点不开")
                    continue
                if info["name"] in ("?", "") or len(info["name"]) < 2:
                    print(f"    ！{card['pos']} 名字没读到，不入库")
                    continue
                if sig is not None:
                    seen_sigs[sig] = info["name"]
                name = info["name"]
                if name in found:
                    continue
                found[name] = info
                new_here += 1
                print(f"  [{len(found)}] {info['rarity']} {name}"
                      f"｜{info['position']}｜{info['style']}｜{info['stats']}")
            if skipped:
                print(f"    （按立绘指纹跳过 {skipped} 张已采过的卡，未点击）")
            stagnant = stagnant + 1 if new_here == 0 else 0
            if stagnant >= 2:
                print("连续两屏没有新角色 → 到底")
                break
            # 每次只滚 1 次拖动（实测内容移动 ~0.095 屏高，约 1/3 行）：
            # 配合立绘指纹去重，宁可多滚几屏，也不跳过同区的卡
            for _ in range(1):
                screen.swipe(w * 0.5, h * 0.78, w * 0.5, h * 0.48, steps=28, hold=0.02)
    finally:
        screen.close()

    print(f"\n共采到 {len(found)} 个角色；总点击 {click_count['n']} 次"
          f"（按立绘去重后，每张新卡平均 {click_count['n'] / max(1, len(found)):.1f} 次）")
    by_rarity = {}
    for info in found.values():
        by_rarity.setdefault(info["rarity"], []).append(info)
    for rarity, items in sorted(by_rarity.items()):
        path = os.path.join(OUT_DIR, f"{rarity.lower()}_roster.json")
        merged, order = {}, []
        if os.path.exists(path):
            try:
                for r in json.load(open(path, encoding="utf-8")):
                    if r.get("name") and r["name"] not in merged:
                        merged[r["name"]] = r
                        order.append(r["name"])
            except Exception as e:                   # noqa: BLE001
                print("旧文件读不出来：", e)
        for r in items:
            if r["name"] not in merged:
                order.append(r["name"])
            merged[r["name"]] = r
        out = [merged[n] for n in order if n]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"{rarity}: 本次 {len(items)} 个，累计 {len(out)} 个 → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())



