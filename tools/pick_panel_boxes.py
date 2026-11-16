"""标注六维面板的元素框（一次拖一个框，按 1~5 指定类型）。

用法：
    python tools/pick_panel_boxes.py 某张面板图.jpg

操作：
    鼠标左键拖拽   画一个框
    1 名字  2 评级  3 数值/上限  4 红字增量  5 图标
    数字键        给刚画的框打标签（不打就是 unlabeled）
    Backspace     删掉最后一个框
    Enter         保存到 panel_boxes.json 并退出
    Esc           不保存退出

为什么要有这个工具（2026-11-17 用户口径）：
    面板上「评级 + 当前/上限 + 红字」经常被 OCR 并成一个框（`688/+80` 的真实
    增量是 +8），靠文本分不开。人工框一次，之后按框裁图 + 颜色识别就不用猜。
"""
import json
import os
import sys

from PIL import Image
from PySide6 import QtCore, QtGui, QtWidgets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "configs", "panel_boxes.json")
LABELS = {"1": "name", "2": "grade", "3": "value", "4": "gain", "5": "icon"}


class Pick(QtWidgets.QWidget):
    def __init__(self, path):
        super().__init__()
        self.setWindowTitle("拖框标注（1名字 2评级 3数值 4红字 5图标）")
        self.img = Image.open(path).convert("RGB")
        self.qimg = QtGui.QImage(self.img.tobytes(), self.img.width,
                                 self.img.height, QtGui.QImage.Format_RGB888)
        self.boxes = []          # [(x, y, w, h, label)]
        self.start = None
        self.cur = None
        self.resize(self.img.width, self.img.height)

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.drawImage(0, 0, self.qimg)
        pen = QtGui.QPen(QtGui.QColor("#ff4040"), 2)
        p.setPen(pen)
        f = p.font()
        f.setPointSize(11)
        p.setFont(f)
        for x, y, w, h, label in self.boxes:
            p.drawRect(x, y, w, h)
            p.drawText(x + 3, max(14, y - 3), label)
        if self.start and self.cur:
            p.drawRect(QtCore.QRect(self.start, self.cur))

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.RightButton:
            # 右键：删掉点中位置最后画的那个框（画错时最常用的撤销）
            pos = ev.pos()
            for i in range(len(self.boxes) - 1, -1, -1):
                x, y, w, h, _lab = self.boxes[i]
                if x <= pos.x() <= x + w and y <= pos.y() <= y + h:
                    self.boxes.pop(i)
                    self.update()
                    return
            return
        self.start = ev.pos()
        self.cur = ev.pos()
        self.update()

    def mouseMoveEvent(self, ev):
        if self.start:
            self.cur = ev.pos()
            self.update()

    def mouseReleaseEvent(self, ev):
        if not self.start:
            return
        r = QtCore.QRect(self.start, ev.pos()).normalized()
        self.start = self.cur = None
        if r.width() > 3 and r.height() > 3:
            self.boxes.append([r.x(), r.y(), r.width(), r.height(), "unlabeled"])
        self.update()

    def keyPressEvent(self, ev):
        k = ev.key()
        text = ev.text()
        if text in LABELS and self.boxes:
            self.boxes[-1][4] = LABELS[text]
            self.update()
        elif k == QtCore.Qt.Key_Backspace and self.boxes:
            self.boxes.pop()
            self.update()
        elif k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            w, h = self.img.width, self.img.height
            data = [{"label": b[4],
                     "x": round(b[0] / w, 4), "y": round(b[1] / h, 4),
                     "w": round(b[2] / w, 4), "h": round(b[3] / h, 4)}
                    for b in self.boxes]
            with open(OUT, "w", encoding="utf-8") as fh:
                json.dump({"src_h": self.img.height, "boxes": data}, fh,
                          ensure_ascii=False, indent=2)
            print("saved", OUT, len(data))
            self.close()
        elif k == QtCore.Qt.Key_Escape:
            self.close()


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not os.path.exists(path):
        print("用法: python tools/pick_panel_boxes.py <面板图.jpg>")
        return 1
    app = QtWidgets.QApplication(sys.argv)
    w = Pick(path)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
