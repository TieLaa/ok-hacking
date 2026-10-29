"""离线检查两个新判据：商店品质色 / 每日商店入口。"""
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.check_handlers import DryRunTask, load_boxes  # noqa: E402


def run(path, fn):
    frame = cv2.imread(path)
    boxes = load_boxes(path, {"ocr": None}, use_cache=True)
    task = DryRunTask(frame, boxes)
    out = fn(task, boxes)
    print(f"\n== {os.path.basename(path)} ==")
    for line in task.logs:
        print("  log:", line)
    print("  result:", out)


def show_items(task, boxes):
    money = task.read_money(boxes)
    items = task.read_shop_items(boxes)
    print(f"  零用钱 {money}")
    for i in items:
        print(f"    {i['name']}  {i['price']}  {i['quality']}  H={i['hue']:.0f} S={i['sat']:.0f}")
    return [i["name"] for i in items]


run(os.path.join(ROOT, "docs", "shots", "2026-10-26-shop.png"), show_items)
