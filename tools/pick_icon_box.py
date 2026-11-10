"""框选头像生成图标（v2：固定大小方框，拖着走，不要求拖边）。

v1 的自由拖边在某些机器上收不到鼠标事件，改成「整块方块跟着鼠标走」：

操作：
    鼠标左键按住拖动   移动方块
    方向键             微调 1px（Shift+方向 = 10px）
    + / -              放大 / 缩小
    n / p              下一张 / 上一张截图
    Enter              确认，生成 icons/icon.png + icon.ico
    Esc                退出
"""

import glob
import os
import sys

import cv2
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_PNG = os.path.join(ROOT, "icons", "icon.png")
ICON_ICO = os.path.join(ROOT, "icons", "icon.ico")

WINDOW = "pick icon  [drag] move  [wasd] nudge  [+/-] size  [n/p] frame  [Enter] save"
STATE = {
    "i": 0,
    "frame": None,
    "cx": 0,
    "cy": 0,
    "side": 260,
    "grab": None,      # 鼠标按下时 光标与方框中心的偏移
}


def clamp_box():
    f = STATE["frame"]
    h, w = f.shape[:2]
    half = STATE["side"] // 2
    STATE["cx"] = min(max(STATE["cx"], half), w - half)
    STATE["cy"] = min(max(STATE["cy"], half), h - half)


def on_mouse(event, x, y, _flags, _param):
    if STATE["frame"] is None:
        return
    if event == cv2.EVENT_LBUTTONDOWN:
        STATE["grab"] = (x - STATE["cx"], y - STATE["cy"])
    elif event == cv2.EVENT_MOUSEMOVE and STATE["grab"] is not None:
        dx, dy = STATE["grab"]
        STATE["cx"], STATE["cy"] = x - dx, y - dy
        clamp_box()
    elif event == cv2.EVENT_LBUTTONUP:
        STATE["grab"] = None


def box():
    half = STATE["side"] // 2
    return (STATE["cx"] - half, STATE["cy"] - half, STATE["cx"] + half, STATE["cy"] + half)


def save_icon(frame, b):
    x0, y0, x1, y1 = b
    crop = frame[y0:y1, x0:x1]
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb).convert("RGBA").resize((320, 320), Image.LANCZOS)
    out = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
    mask = Image.new("L", (320, 320), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 319, 319), fill=255)
    out.paste(img, (0, 0), mask)
    out = out.resize((256, 256), Image.LANCZOS)
    out.save(ICON_PNG)
    out.save(ICON_ICO, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return x0, y0, x1, y1


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.environ.get("TEMP", "."), "opencode", "icon_shots")
    paths = sorted(glob.glob(os.path.join(folder, "*.jpg")) + glob.glob(os.path.join(folder, "*.png")))
    if not paths:
        raise SystemExit(f"没有截图：{folder}")

    STATE["frame"] = cv2.imread(paths[0])
    h, w = STATE["frame"].shape[:2]
    STATE["cx"], STATE["cy"] = w // 2, h // 2     # 默认画面正中
    clamp_box()

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW, on_mouse)

    while True:
        b = box()
        view = STATE["frame"].copy()
        cv2.rectangle(view, (b[0], b[1]), (b[2], b[3]), (0, 255, 0), 2)
        cv2.putText(view, f"{STATE['i'] + 1}/{len(paths)}  {os.path.basename(paths[STATE['i']])}"
                          f"   {STATE['side']}px", (16, 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.imshow(WINDOW, view)
        key = cv2.waitKey(20) & 0xFF

        if key == 27:
            break
        if key in (13, 10):
            x0, y0, x1, y1 = save_icon(STATE["frame"], b)
            print(f"BOX frame={os.path.basename(paths[STATE['i']])} "
                  f"x={x0} y={y0} side={x1 - x0}")
            print(f"saved: {ICON_PNG}")
            break
        if key in (ord("n"), ord("p")):
            STATE["i"] = (STATE["i"] + (1 if key == ord("n") else -1)) % len(paths)
            STATE["frame"] = cv2.imread(paths[STATE["i"]])
            clamp_box()
        elif key in (ord("a"), ord("d"), ord("w"), ord("s"), ord("A"), ord("D"), ord("W"), ord("S")):
            step = 50 if key in (ord("A"), ord("D"), ord("W"), ord("S")) else 5
            if key in (ord("a"), ord("A")):
                STATE["cx"] -= step
            elif key in (ord("d"), ord("D")):
                STATE["cx"] += step
            elif key in (ord("w"), ord("W")):
                STATE["cy"] -= step
            else:
                STATE["cy"] += step
            clamp_box()
        elif key in (ord("+"), ord("=")):
            STATE["side"] = min(STATE["side"] + 10, 500)
            clamp_box()
        elif key in (ord("-"), ord("_")):
            STATE["side"] = max(STATE["side"] - 10, 60)
            clamp_box()

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
