"""框选头像生成图标（v3：Qt 版）。

前两版都基于 OpenCV HighGUI，鼠标事件在这台机器上都收不到：
v1 拖边、v2 整块跟鼠标，都表现为「拖不动」。HighGUI 的鼠标回调在
部分 Windows 环境下直接不进来，改参数救不回来，所以换 PySide6
（项目本来就有，GUI 也是它）。

操作：
    鼠标左键拖动        移动圆框（点在框外 = 直接把框挪到点上）
    滚轮 / + / -        放大 / 缩小
    W/A/S/D             大调 50px
    方向键              微调 1px（Shift+方向 = 10px）
    n / p               下一张 / 上一张截图
    Enter               确认，生成 icons/icon.png + icon.ico
    Esc                 退出
"""

import glob
import os
import sys

from PIL import Image, ImageDraw
from PySide6 import QtCore, QtGui, QtWidgets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_PNG = os.path.join(ROOT, "icons", "icon.png")
ICON_ICO = os.path.join(ROOT, "icons", "icon.ico")

MIN_SIDE = 60
MAX_SIDE = 900


def pil_to_qimage(img):
    """PIL(RGBA) → QImage；必须 copy()，否则 bytes 被回收后画面是黑的。"""
    img = img.convert("RGBA")
    return QtGui.QImage(img.tobytes("raw", "RGBA"), img.width, img.height,
                        QtGui.QImage.Format_RGBA8888).copy()


def mask_circle(img, size=256):
    """居中裁成正方形 → 缩放 → 圆形遮罩（与存盘成品同一套参数）。"""
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((320, 320), Image.LANCZOS)
    out = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
    mask = Image.new("L", (320, 320), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 319, 319), fill=255)
    out.paste(img, (0, 0), mask)
    return out.resize((size, size), Image.LANCZOS)


def save_icon(img, b):
    """按框裁图 → 圆形遮罩 → 写 png + ico（ico 保留多尺寸）。"""
    x0, y0, x1, y1 = b
    out = mask_circle(img.crop((x0, y0, x1, y1)))
    out.save(ICON_PNG)
    out.save(ICON_ICO, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return x0, y0, x1, y1


class Canvas(QtWidgets.QWidget):
    """左半边：显示整帧 + 圆框，负责鼠标拖动。"""

    moved = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(880, 560)
        self.img = None
        self.cx = self.cy = 0
        self.side = 300
        self.grab = None
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def set_image(self, img):
        self.img = img
        self.cx, self.cy = img.width // 2, img.height // 2
        self.clamp()
        self.update()
        self.moved.emit()

    def box(self):
        half = self.side // 2
        return (self.cx - half, self.cy - half, self.cx + half, self.cy + half)

    def clamp(self):
        if not self.img:
            return
        half = self.side // 2
        self.cx = min(max(self.cx, half), max(self.img.width - half, half))
        self.cy = min(max(self.cy, half), max(self.img.height - half, half))

    def set_side(self, side):
        self.side = int(min(max(side, MIN_SIDE), MAX_SIDE))
        self.clamp()
        self.update()
        self.moved.emit()

    def scale(self):
        """图片 → 控件的缩放比例（保持比例，居中留边）。"""
        if not self.img:
            return 1.0, 0.0, 0.0
        s = min(self.width() / self.img.width, self.height() / self.img.height)
        ox = (self.width() - self.img.width * s) / 2
        oy = (self.height() - self.img.height * s) / 2
        return s, ox, oy

    def to_image(self, pos):
        s, ox, oy = self.scale()
        return (pos.x() - ox) / s, (pos.y() - oy) / s

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor("#15181c"))
        if not self.img:
            return
        s, ox, oy = self.scale()
        rect = QtCore.QRectF(ox, oy, self.img.width * s, self.img.height * s)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        p.drawImage(rect, pil_to_qimage(self.img))

        # 圆框外的部分压暗，让「最后裁到哪」一眼可见
        x0, y0, x1, y1 = self.box()
        circle = QtCore.QRectF(ox + x0 * s, oy + y0 * s, (x1 - x0) * s, (y1 - y0) * s)
        veil = QtGui.QPainterPath()
        veil.addRect(rect)
        cut = QtGui.QPainterPath()
        cut.addEllipse(circle)
        p.fillPath(veil.subtracted(cut), QtGui.QColor(0, 0, 0, 120))
        p.setPen(QtGui.QPen(QtGui.QColor("#22d3ee"), 2))
        p.drawEllipse(circle)
        p.setPen(QtGui.QColor("#b9bfca"))
        p.drawText(12, 22, f"({x0}, {y0})  side={x1 - x0}  拖动圆心 / 滚轮改大小")

    def mousePressEvent(self, e):
        if not self.img or e.button() != QtCore.Qt.LeftButton:
            return
        ix, iy = self.to_image(e.position())
        half = self.side // 2
        inside = abs(ix - self.cx) <= half and abs(iy - self.cy) <= half
        if inside:
            self.grab = (ix - self.cx, iy - self.cy)
        else:
            self.cx, self.cy = int(ix), int(iy)
            self.grab = (0, 0)
            self.clamp()
            self.update()
            self.moved.emit()

    def mouseMoveEvent(self, e):
        if self.grab is None:
            return
        ix, iy = self.to_image(e.position())
        self.cx, self.cy = int(ix - self.grab[0]), int(iy - self.grab[1])
        self.clamp()
        self.update()
        self.moved.emit()

    def mouseReleaseEvent(self, _e):
        self.grab = None

    def wheelEvent(self, e):
        self.set_side(self.side + (20 if e.angleDelta().y() > 0 else -20))

    def keyPressEvent(self, e):
        k = e.key()
        big = 50 if not (e.modifiers() & QtCore.Qt.ShiftModifier) else 200
        step = 10 if e.modifiers() & QtCore.Qt.ShiftModifier else 1
        if k in (QtCore.Qt.Key_W,):
            self.cy -= big
        elif k in (QtCore.Qt.Key_S,):
            self.cy += big
        elif k in (QtCore.Qt.Key_A,):
            self.cx -= big
        elif k in (QtCore.Qt.Key_D,):
            self.cx += big
        elif k == QtCore.Qt.Key_Up:
            self.cy -= step
        elif k == QtCore.Qt.Key_Down:
            self.cy += step
        elif k == QtCore.Qt.Key_Left:
            self.cx -= step
        elif k == QtCore.Qt.Key_Right:
            self.cx += step
        elif k in (QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal):
            self.set_side(self.side + 10)
            return
        elif k in (QtCore.Qt.Key_Minus, QtCore.Qt.Key_Underscore):
            self.set_side(self.side - 10)
            return
        else:
            e.ignore()
            return
        self.clamp()
        self.update()
        self.moved.emit()


class Picker(QtWidgets.QWidget):
    def __init__(self, paths, visible=True):
        super().__init__()
        self.setWindowTitle("pick icon — 拖动圆心 / 滚轮改大小 / n·p 换帧 / Enter 保存")
        self.paths = paths
        self.i = 0

        self.canvas = Canvas()
        self.canvas.moved.connect(self.refresh_preview)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(10)
        self.big = self._preview_label(256)
        self.mid = self._preview_label(64)
        self.small = self._preview_label(32)
        self.info = QtWidgets.QLabel()
        self.info.setStyleSheet("color:#c9d1d9; font-size:12px")
        self.info.setWordWrap(True)
        right.addWidget(self.big, 0, QtCore.Qt.AlignHCenter)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.mid)
        row.addWidget(self.small)
        row.addStretch(1)
        right.addLayout(row)
        right.addWidget(self.info)
        right.addStretch(1)
        panel = QtWidgets.QWidget()
        panel.setLayout(right)
        panel.setFixedWidth(300)

        root = QtWidgets.QHBoxLayout(self)
        root.addWidget(self.canvas, 1)
        root.addWidget(panel, 0)

        self.canvas.set_image(Image.open(paths[0]).convert("RGB"))
        self.refresh_preview()
        self.resize(1250, 720)
        if visible:
            self.show()
            self.raise_()
            self.activateWindow()
            self.canvas.setFocus()

    @staticmethod
    def _preview_label(size):
        lab = QtWidgets.QLabel()
        lab.setFixedSize(size, size)
        lab.setStyleSheet("background:#2b3138; border:1px solid #3d444d")
        return lab

    def refresh_preview(self):
        x0, y0, x1, y1 = self.canvas.box()
        crop = self.canvas.img.crop((x0, y0, x1, y1))
        self.big.setPixmap(QtGui.QPixmap.fromImage(pil_to_qimage(mask_circle(crop, 256))))
        self.mid.setPixmap(QtGui.QPixmap.fromImage(pil_to_qimage(mask_circle(crop, 64))))
        self.small.setPixmap(QtGui.QPixmap.fromImage(pil_to_qimage(mask_circle(crop, 32))))
        self.info.setText(f"第 {self.i + 1}/{len(self.paths)} 张\n"
                          f"{os.path.basename(self.paths[self.i])}\n"
                          f"框 {x1 - x0}px  @ ({x0}, {y0})")

    def load(self, i):
        self.i = i % len(self.paths)
        self.canvas.set_image(Image.open(self.paths[self.i]).convert("RGB"))

    def keyPressEvent(self, e):
        k = e.key()
        if k == QtCore.Qt.Key_Escape:
            self.close()
        elif k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            x0, y0, x1, y1 = save_icon(self.canvas.img, self.canvas.box())
            print(f"BOX frame={os.path.basename(self.paths[self.i])} "
                  f"x={x0} y={y0} side={x1 - x0} saved={ICON_PNG}", flush=True)
            self.close()
        elif k == QtCore.Qt.Key_N:
            self.load(self.i + 1)
        elif k == QtCore.Qt.Key_P:
            self.load(self.i - 1)
        else:
            self.canvas.keyPressEvent(e)


def collect(folder):
    return sorted(glob.glob(os.path.join(folder, "*.jpg")) + glob.glob(os.path.join(folder, "*.png")))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    folder = args[0] if args else os.path.join(
        os.environ.get("TEMP", "."), "opencode", "icon_shots")
    paths = collect(folder)
    if not paths:
        raise SystemExit(f"没有截图：{folder}")

    app = QtWidgets.QApplication(sys.argv[:1])
    if "--check" in sys.argv:
        # 无头自检：不显示窗口，只验证「读帧 → 裁剪 → 圆形遮罩 → 预览」整条链路能出图
        picker = Picker(paths, visible=False)
        picker.refresh_preview()
        assert not picker.big.pixmap().isNull(), "预览图为空"
        print(f"check ok: {len(paths)} frames, first={os.path.basename(paths[0])} "
              f"preview={picker.big.pixmap().width()}px", flush=True)
        return 0

    picker = Picker(paths)
    picker.raise_()
    picker.activateWindow()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
