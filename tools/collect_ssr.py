"""采集卡池「卡池详情」里所有 SSR 角色的档案（2026-10-28 用户委托）。

⚠️ 2026-10-30：本文件的**滚动逻辑已废弃**（固定两下大拖动，会直接越过 SR 区落到最底部
R 区，导致「只采到几个」）。卡池详情页（SSR/SR/R 同页）请用 `tools/collect_roster.py`。
保留本文件是因为它的字段解析和「点原型图标读技能面板」还在被复用。

采集字段（用户在截图里逐个点出来教的）：
- 名字 / 稀有度（SSR_ 前缀）
- **风格**：名字右侧的圆形图标 —— 蓝色闪电=速度、绿色星星=技巧、红色拳头=力量（互相克制）
- **位置**：风格下面的粉标签（分卫 / 控卫 / 小前 / 大前 / 中锋）
- 技能标签：位置旁边的绿标签（如「得分手」）
- **技能**：点右下角原型图标（小人 + 「训练」）弹出的面板
  - 技能名（表情一致）、流派（训练流）
  - 训练技能正文
  - 升星效果（逐星级条目）

流程：
  卡池详情 → 点第 N 张卡 → 角色页（采名字/风格/位置/标签）→ 点右下原型图标
  → 面板（采技能/流派/训练技能/升星效果）→ 点空白关面板 → 左上角 « 返回两次 → 下一张

用法：
    .venv\\Scripts\\python.exe tools\\collect_ssr.py                 # 全量，写到 docs/data/ssr_roster.json
    .venv\\Scripts\\python.exe tools\\collect_ssr.py --limit 2       # 只采前 2 个（调试）
    .venv\\Scripts\\python.exe tools\\collect_ssr.py --from-dump DIR # 只解析已存的帧（不点游戏）

退出码：0 = 采到至少一个角色；1 = 一个都没采到。
"""

import argparse
import json
import os
import re
import sys
import time

import cv2
import numpy as np
import win32api
import win32con
import win32gui

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.abs_mouse import (click_client, ensure_foreground,  # noqa: E402
                             find_game_hwnd, move_to_screen)
from tools.wgc_capture import MockWindow, imwrite_auto  # noqa: E402

POSITIONS = ("分卫", "控卫", "小前", "大前", "中锋")
OUT_JSON = os.path.join(ROOT, "docs", "roster", "ssr_roster.json")


class Screen:
    """持续抓帧 + OCR（复用 ok-script 的 WGC 实现，跟 wgc_capture.py 一致）。"""

    def __init__(self, hwnd):
        # 2026-11-01：删掉没用过的 `ocr_every` 参数（vulture 唯一一处真死参数：
        # 构造时存进 self 都没做，全仓也没有调用点传它）。要加节流就该真实现，
        # 而不是留一个「看着像能配」的空参数。
        from ok.device.capture_methods.windows_graphics import (
            WindowsGraphicsCaptureMethod)
        self.hwnd = hwnd
        self.cap = WindowsGraphicsCaptureMethod(MockWindow(hwnd))
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr
        self.ocr = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True,
                                 show_log=False)

    def frame(self, tries=12):
        for _ in range(tries):
            f = self.cap.get_frame()
            if f is not None:
                return f
            time.sleep(0.2)
        return None

    def boxes(self, frame=None):
        frame = self.frame() if frame is None else frame
        if frame is None:
            return [], None, None
        h, w = frame.shape[:2]
        res = self.ocr.ocr(frame)
        page = res[0] if isinstance(res, list) and len(res) == 1 else res
        out = []
        for entry in page or []:
            try:
                poly, (text, score) = entry[0], entry[1]
            except Exception:                                  # noqa: BLE001
                continue
            if not text or score is None or score < 0.35:
                continue
            xs = [int(p[0]) for p in poly]
            ys = [int(p[1]) for p in poly]
            out.append({"text": text.strip(), "x": min(xs), "y": min(ys),
                        "w": max(xs) - min(xs), "h": max(ys) - min(ys)})
        return out, w, h

    def click(self, x, y, after=0.6):
        ensure_foreground(self.hwnd)
        click_client(self.hwnd, int(x), int(y))
        time.sleep(after)

    def swipe(self, x1, y1, x2, y2, steps=24, hold=0.02):
        """按住往上拖 = 列表往下滚。

        坑（2026-10-28）：分步移动太碎、间隔太短，游戏只认成"点了一下"，
        列表只挪几个 px。现在按住后先停 0.1s 让它变成 drag，再匀速拖到位，
        松手前再停 0.05s。
        """
        ensure_foreground(self.hwnd)
        sx1, sy1 = win32gui.ClientToScreen(self.hwnd, (int(x1), int(y1)))
        sx2, sy2 = win32gui.ClientToScreen(self.hwnd, (int(x2), int(y2)))
        move_to_screen(sx1, sy1)
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        try:
            time.sleep(0.10)
            for i in range(1, steps + 1):
                t = i / steps
                move_to_screen(int(sx1 + (sx2 - sx1) * t),
                               int(sy1 + (sy2 - sy1) * t), settle=0)
                time.sleep(hold)
            time.sleep(0.05)
        finally:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(1.0)

    def wheel(self, x, y, clicks=-3):
        """在对话框里滚动（卡池的角色列表是滚动区，往下还有两行）。"""
        ensure_foreground(self.hwnd)
        sx, sy = win32gui.ClientToScreen(self.hwnd, (int(x), int(y)))
        move_to_screen(sx, sy)
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, 120 * clicks, 0)
        time.sleep(0.9)

    def close(self):
        try:
            self.cap.close()
        except Exception:                                      # noqa: BLE001
            pass


def joined(boxes):
    return "".join(b["text"] for b in boxes)


def find(boxes, *keys, region=None, w=None, h=None):
    """按子串找框，可按比例区域过滤。(rx0, ry0, rx1, ry1)"""
    for b in boxes:
        if not any(k in b["text"] for k in keys):
            continue
        if region and w and h:
            cx, cy = (b["x"] + b["w"] / 2) / w, (b["y"] + b["h"] / 2) / h
            if not (region[0] <= cx <= region[2] and region[1] <= cy <= region[3]):
                continue
        return b
    return None


def style_of(frame, w, h, name_box):
    """名字右侧的圆形风格图标：蓝=速度 / 绿=技巧 / 红=力量。"""
    if name_box is None:
        return "?"
    x1 = int(w * ((name_box["x"] + name_box["w"]) / w + 0.005))
    x2 = min(w, x1 + int(w * 0.055))
    y1 = max(0, int(name_box["y"] - h * 0.02))
    y2 = min(h, int(name_box["y"] + name_box["h"] + h * 0.02))
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return "?"
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    m = (sat > 90) & (val > 110)
    if int(m.sum()) < 40:
        return "?"
    hmed = float(np.median(hue[m]))
    if 90 <= hmed <= 130:
        return "速度"          # 蓝
    if 35 <= hmed < 90:
        return "技巧"          # 绿
    if hmed <= 12 or hmed >= 165:
        return "力量"          # 红
    if 12 < hmed < 35:
        return "力量"          # 橙红兜底
    return f"?H{int(hmed)}"


def parse_pool_cards(boxes, w, h):
    """卡池详情页：按位置标签定位每张卡（标签在卡片顶部）。"""
    cards = []
    for b in boxes:
        if b["text"] not in POSITIONS:
            continue
        cx, cy = (b["x"] + b["w"] / 2) / w, (b["y"] + b["h"] / 2) / h
        if not (0.10 <= cx <= 0.95 and 0.45 <= cy <= 0.95):
            continue
        cards.append({"cx": cx, "cy": cy, "pos": b["text"]})
    cards.sort(key=lambda c: (round(c["cy"] * 20), c["cx"]))
    return cards


def parse_character(boxes, frame, w, h, rarity="SSR"):
    """角色详情页：名字 / 稀有度 / 风格 / 位置 / 技能标签。"""
    name_box = None
    for b in boxes:
        if re.match(rf"^{rarity}", b["text"]):
            name_box = b
            break
    name = name_box["text"] if name_box else "?"
    name = re.sub(rf"^{rarity}[_\-\s]*", "", name).strip() or name
    # 名字被 OCR 拆成两个框（"SSR下" + "雨森琉璃"）时，把同一行右边那块接上
    if name_box is not None and len(name) <= 2:
        cy = name_box["y"] + name_box["h"] / 2
        right = [b for b in boxes
                 if b is not name_box
                 and abs((b["y"] + b["h"] / 2) - cy) < name_box["h"] * 0.8
                 and b["x"] > name_box["x"] + name_box["w"] - 5
                 and not any(ch.isdigit() for ch in b["text"])]
        right.sort(key=lambda b: b["x"])
        if right and len(right[0]["text"].strip()) > len(name):
            # 左边那一截是 OCR 噪声（例：「SSR下」+「雨森琉璃」，真名是「雨森琉璃」）
            name = right[0]["text"].strip()
    rarity = rarity if name_box else "?"
    pos = next((b["text"] for b in boxes if b["text"] in POSITIONS), "?")
    # 技能标签（如「得分手」）= 与位置同一行的另一个短词，不能带数字（避开 LV 1/10）
    tag = None
    pos_box = next((b for b in boxes if b["text"] in POSITIONS), None)
    if pos_box is not None:
        pcy = pos_box["y"] + pos_box["h"] / 2
        for b in boxes:
            t = b["text"]
            if t in POSITIONS or t.startswith(rarity) or any(ch.isdigit() for ch in t):
                continue
            if not (2 <= len(t) <= 5):
                continue
            cx = (b["x"] + b["w"] / 2) / w
            cy = b["y"] + b["h"] / 2
            if abs(cy - pcy) <= h * 0.02 and cx >= 0.60:
                tag = t
                break
    return {
        "name": name, "rarity": rarity,
        "style": style_of(frame, w, h, name_box),
        "position": pos, "tag": tag,
    }


def parse_skill_panel(boxes, w, h, rarity="SSR"):
    """原型图标弹出的面板：技能名 / 流派 / 训练技能 / 升星效果。"""
    panel = []
    for b in boxes:
        cx = (b["x"] + b["w"] / 2) / w
        cy = (b["y"] + b["h"] / 2) / h
        if cx < 0.66 or cy < 0.135 or cy > 0.86:
            continue
        if b["text"] in ("满级", "OFF", "详情", "升星", "天赋", "特质",
                         "显示风格", "满级"):
            continue
        if b["text"] in POSITIONS or b["text"].startswith(rarity):
            continue
        if any(ch.isdigit() for ch in b["text"]) and len(b["text"]) <= 5:
            continue
        panel.append(b)
    panel.sort(key=lambda b: (b["y"], b["x"]))
    lines = [b["text"] for b in panel]
    # 流派（训练流 / 商店流 / 扭蛋流 / 小游戏流…）：面板里唯一「以 流 结尾」的短词
    style_tag = "?"
    for b in panel:
        t = b["text"]
        if t.endswith("流") and 2 <= len(t) <= 4:
            style_tag = t
            break
    # 技能名 = 面板第一行，但要排除单字噪声、流派词、以及训练技能正文
    skill = "?"
    for b in panel:
        t = b["text"]
        if len(t) < 2 or t == style_tag or t.endswith("流"):
            continue
        if t.startswith("训练技能") or t.startswith("升星效果"):
            continue
        skill = t
        break
    train, stars, mode = [], [], None
    for b in panel:
        t = b["text"]
        if t.startswith("训练技能"):
            mode = "train"
            continue
        if t.startswith("升星效果"):
            mode = "star"
            continue
        if t == skill or t == style_tag:
            continue
        if mode == "train":
            train.append(t)
        elif mode == "star":
            stars.append(t)
    return {
        "skill_name": skill, "style_tag": style_tag,
        "train_skill": "".join(train),
        "star_effects": stars,
        "raw_panel": lines,
    }


def collect(screen, limit=None, pause=0.8, verbose=False, no_scroll=False,
            rarity="SSR", max_minutes=10.0):
    """状态机采集：卡池页按顺序点卡 → 角色页采基础信息 → 点原型图标采技能 → 返回。

    关键点（都是实跑踩出来的）：
    - 点卡/返回都用 OCR 坐标，不按比例猜（截图带系统标题栏，y 会差 30px）
    - 第二行卡片被对话框切掉半截，点卡要试几个偏移
    - 同一画面连续 10 拍没变化就停，别发呆
    - 滚动是否真的动了，看**第一张卡的位置**而不是整段 OCR
      （2026-10-30：整段 OCR 可能因为动画/飘字变化，但列表没动，会假判成「滚动了」）
    - 连续两次滚动都没动 = 到底了；另外有 max_minutes 总超时，绝对不会无限跑
    """
    rows, seen = [], set()
    stage = None            # None=待采 / "icon"=已点图标等面板 / "panel"=面板已采
    card_idx = 0
    icon_tries = 0
    guard = 0
    last_txt, same_ticks = None, 0
    scrolls = 0
    scroll_fail = 0
    deadline = time.time() + max_minutes * 60

    def read():
        boxes, w, h = screen.boxes()
        return boxes, w, h, joined(boxes)

    def click_back():
        boxes, w, h = screen.boxes()
        glyph = next((b for b in boxes
                      if b["x"] < w * 0.12 and b["y"] < h * 0.15
                      and len(b["text"]) <= 2), None)
        if glyph is not None:
            screen.click(glyph["x"] + glyph["w"] / 2,
                         glyph["y"] + glyph["h"] / 2, after=pause)
        else:
            screen.click(w * 0.046, h * 0.067, after=pause)

    def click_icon():
        boxes, w, h = screen.boxes()
        label = next((b for b in boxes if b["text"] == "训练"
                      and (b["x"] + b["w"] / 2) > w * 0.85), None)
        if label is not None:
            screen.click(label["x"] + label["w"] / 2,
                         label["y"] + label["h"] / 2 - h * 0.076, after=pause)
        else:
            screen.click(w * 0.952, h * 0.700, after=pause)

    def at_pool(txt):
        # 注意：招募页上也有一个叫「卡池详情」的**按钮**，不能只看这个词
        # （2026-10-28 实跑：在招募页被误判成卡池详情，解析不到卡片就退出了）
        return ("角色一览" in txt) or ("SSR角色一览" in txt) or \
               ("显示风格" in txt and "历史记录" in txt)

    def back_to_pool(max_tries=5):
        for _ in range(max_tries):
            if at_pool(read()[3]):
                return True
            click_back()
        return at_pool(read()[3])

    while guard < 400:
        guard += 1
        if time.time() > deadline:
            print(f"！超过 {max_minutes:.0f} 分钟上限，收工（已采 {len(rows)} 个）")
            return rows
        boxes, w, h, txt = read()
        if not boxes:
            time.sleep(1.0)
            continue
        if verbose:
            print("   OCR:", txt[:150])
        if txt == last_txt:
            same_ticks += 1
            if same_ticks >= 10:
                print(f"！连续 {same_ticks} 拍没进展，停止。当前 OCR：{txt[:160]}")
                return rows
        else:
            last_txt, same_ticks = txt, 0

        # 1) 技能面板
        if ("训练技能" in txt) or ("升星效果" in txt):
            if stage != "panel" and rows:
                info = parse_skill_panel(boxes, w, h, rarity)
                rows[-1].update(info)
                print(f"    技能：{info['skill_name']}｜{info['style_tag']}"
                      f"｜训练技能 {len(info['train_skill'])} 字"
                      f"｜升星条目 {len(info['star_effects'])}")
            stage = "panel"
            screen.click(w * 0.30, h * 0.45, after=pause)     # 点空白关面板
            continue

        # 2) 角色详情页
        if ("升星" in txt and "天赋" in txt):
            if stage == "panel":
                if not back_to_pool():
                    print("！回不到卡池页，停止")
                    return rows
                stage = None
                card_idx += 1
                if limit is not None and len(rows) >= limit:
                    print("达到 --limit，收工")
                    return rows
                continue
            if stage is None:
                base = parse_character(boxes, screen.frame(), w, h, rarity)
                if base["name"] in seen or base["name"] == "?":
                    print(f"    （{base['name']} 已采过，跳过）")
                    if not back_to_pool():
                        return rows
                    card_idx += 1
                    continue
                seen.add(base["name"])
                rows.append(base)
                print(f"  [{len(rows)}] {base['rarity']} {base['name']}"
                      f"｜风格 {base['style']}｜位置 {base['position']}"
                      f"｜标签 {base['tag']}")
                stage, icon_tries = "icon", 1
                click_icon()
                continue
            icon_tries += 1
            if icon_tries <= 3:
                print(f"    面板没出现，再点一次（{icon_tries}/3）")
                click_icon()
            else:
                print("    ！面板始终没弹出，跳过该角色")
                if not back_to_pool():
                    return rows
                stage = None
                card_idx += 1
            continue

        # 3) 卡池详情页
        if at_pool(txt):
            cards = parse_pool_cards(boxes, w, h)
            if not cards:
                print("！卡池页没定位到卡片：", txt[:120])
                return rows
            if card_idx >= len(cards):
                # --no-scroll：这一屏采完就收工，翻页交给用户手动做
                if no_scroll:
                    print(f"这一屏的 {len(cards)} 张卡都采完了（未翻页）")
                    return rows
                # 列表还要往下滚（用户 2026-10-28：下面还有两行 SSR）
                if scrolls >= 12:
                    print("滚动次数到上限，停止")
                    return rows
                before_top = cards[0]["cy"] if cards else None
                # 滚轮对这个对话框无效，改用「按住往上拖」
                screen.swipe(w * 0.5, h * 0.80, w * 0.5, h * 0.38)
                screen.swipe(w * 0.5, h * 0.80, w * 0.5, h * 0.38)
                scrolls += 1
                nb, nw, nh, after_txt = read()
                after_cards = parse_pool_cards(nb, nw, nh) if nb else []
                after_top = after_cards[0]["cy"] if after_cards else None
                moved = (after_top is not None and before_top is not None
                         and abs(after_top - before_top) > 0.01)
                if not moved and after_txt == txt:
                    screen.wheel(w * 0.5, h * 0.55, clicks=-3)   # 再兜底试一次滚轮
                    nb, nw, nh, after_txt = read()
                    after_cards = parse_pool_cards(nb, nw, nh) if nb else []
                    after_top = after_cards[0]["cy"] if after_cards else None
                    moved = (after_top is not None and before_top is not None
                             and abs(after_top - before_top) > 0.01)
                if not moved:
                    scroll_fail += 1
                    print(f"  滚动没动（{scroll_fail}/2）")
                    if scroll_fail >= 2:
                        print("连续两次滚动都没动 → 到列表底部")
                        return rows
                else:
                    scroll_fail = 0
                    print(f"  往下滚一屏（第 {scrolls} 次）")
                card_idx = 0
                continue
            card = cards[card_idx]
            print(f"→ 点第 {card_idx + 1}/{len(cards)} 张卡（{card['pos']}）")
            opened = False
            for dy in (0.13, 0.09, 0.06, 0.045):
                screen.click(w * card["cx"], h * (card["cy"] + dy), after=pause)
                t2 = read()[3]
                if "升星" in t2 and "天赋" in t2:
                    opened = True
                    break
            if not opened:
                print("    ！这张卡点不开，跳过")
                card_idx += 1
            continue

        # 4) 其它页面
        print("… 未知页面，点左上角返回")
        click_back()

    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--rarity", default="SSR", choices=("SSR", "SR", "R"))
    ap.add_argument("--max-minutes", type=float, default=10.0)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--no-scroll", action="store_true",
                    help="只采当前可见的卡片，不尝试滚动列表")
    args = ap.parse_args(argv)
    out_path = args.out or os.path.join(
        ROOT, "docs", "roster", f"{args.rarity.lower()}_roster.json")

    hwnd = find_game_hwnd()
    if not hwnd:
        print("找不到游戏窗口")
        return 1
    print(f"游戏窗口 {hwnd}｜采集 {args.rarity}｜上限 {args.max_minutes:.0f} 分钟")
    screen = Screen(hwnd)
    try:
        rows = collect(screen, limit=args.limit, verbose=args.verbose,
                       no_scroll=args.no_scroll, rarity=args.rarity,
                       max_minutes=args.max_minutes)
    finally:
        screen.close()
    if not rows:
        print("没采到任何角色")
        return 1
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    merged, order = {}, []
    if os.path.exists(out_path):                     # 和已有数据合并（按名字去重）
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
