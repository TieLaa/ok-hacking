"""标注某个科目的「失败率」标签框（一次一个科目）。

用法：
    python tools/pick_fail_boxes.py 整屏截图.jpg 运控

操作：
    鼠标左键拖拽   画一个框（只认最后一个，可反复重画）
    右键点某个框    删掉它
    Backspace      删掉最后一个框
    Enter          保存/合并到 src/tasks/training/strategy/fail_boxes.json
    Esc            不保存退出

为什么：失败率标签跟着「当前选中的那一行」走，位置不固定、还会随列表滚动。
每个科目各框一次，运行时就能按选中科目直接裁图读。
"""
import json
import os
import sys

from PySide6 import QtCore, QtGui, QtWidgets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "src", "tasks", "training", "strategy", "fail_boxes.json")
SUBJECTS = ["投射", "弹跳", "防守", "体能", "运控", "意识"]
SUBJECT = sys.argv[2] if len(sys.argv) > 2 else "运控"


class Pick(QtWidgets.QWidget):
    def __init__(self, path):
        super().__init__()
        self.setWindowTitle("拖框标注失败率（1~6 指定科目 / 右键删除 / Enter 保存）")
        self.img = QtGui.QImage(path)
        self.boxes = []          # [[x, y, w, h, subject], ...]
        self.start = None
        self.cur = None
        self.resize(self.img.width(), self.img.height())

    def _next_subject(self):
        return SUBJECT

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.drawImage(0, 0, self.img)
        p.setPen(QtGui.QPen(QtGui.QColor("#ff4040"), 2))
        f = p.font()
        f.setPointSize(11)
        p.setFont(f)
        for x, y, w, h, subject in self.boxes:
            p.drawRect(x, y, w, h)
            p.drawText(x + 3, max(14, y - 4), subject)
        if self.start and self.cur:
            p.drawRect(QtCore.QRect(self.start, self.cur))
        p.setPen(QtGui.QColor("#ffff00"))
        p.drawText(8, 20, f"当前科目：{SUBJECT}（画一个框，Enter 保存）")

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.RightButton:
            pos = ev.pos()
            for i in range(len(self.boxes) - 1, -1, -1):
                x, y, w, h, _s = self.boxes[i]
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
        if r.width() > 6 and r.height() > 6:
            self.boxes.append([r.x(), r.y(), r.width(), r.height(), self._next_subject()])
        self.update()

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == QtCore.Qt.Key_Backspace and self.boxes:
            self.boxes.pop()
            self.update()
        elif k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if not self.boxes:
                return
            w, h = self.img.width(), self.img.height()
            b = self.boxes[-1]
            data = {}
            if os.path.exists(OUT):
                try:
                    with open(OUT, encoding="utf-8") as fh:
                        old = json.load(fh)
                    for item in old.get("boxes", []):
                        data[item["subject"]] = item
                except Exception:
                    data = {}
            data[SUBJECT] = {"subject": SUBJECT,
                             "x": round(b[0] / w, 4), "y": round(b[1] / h, 4),
                             "w": round(b[2] / w, 4), "h": round(b[3] / h, 4)}
            os.makedirs(os.path.dirname(OUT), exist_ok=True)
            with open(OUT, "w", encoding="utf-8") as fh:
                json.dump({"boxes": list(data.values())}, fh, ensure_ascii=False, indent=2)
            print("saved", OUT, SUBJECT, data[SUBJECT])
            self.close()
        elif k == QtCore.Qt.Key_Escape:
            self.close()


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not os.path.exists(path):
        print("用法: python tools/pick_fail_boxes.py <整屏截图.jpg>")
        return 1
    app = QtWidgets.QApplication(sys.argv)
    win = Pick(path)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
