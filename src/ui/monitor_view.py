"""监控页的纯渲染层：只认 layout 字典 + data 字典，不碰任何业务对象。

拆出来的原因：
1. 布局要能直接用界面改（编辑态）或手改 JSON，不用改 Python；
2. 渲染层可以脱离 ok 框架单独实例化，方便离线截图验证。

数据里的占位符统一写成 `{key}`，缺失值显示 `—`；模板可带 `fallback` 整句兜底。
编辑态下每个区块右上角有上移/下移/隐藏，双击文字可直接改（写回 JSON）。
"""

from __future__ import annotations

import os
import re
from functools import partial

from PySide6.QtCore import QEvent, Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (CheckBox, ComboBox, FluentIcon, PrimaryPushButton,
                            PushButton, SwitchButton)

from ok.ui.qt.common.OKIcon import OKIcon
from ok.util.file import get_relative_path

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
MISSING = "—"
GROUP = "__group__"
ATTR_ICON_DIR = os.path.join("assets", "ui", "attrs")
# 和游戏底部属性面板一致：三行两列，顺序不能改
DEFAULT_ATTRS = ("投射", "弹跳", "运控", "体能", "防守", "意识")


class MonitorActions:
    """页面按钮 + 布局编辑的回调容器。MonitorTab 会把自己的方法挂进来。"""

    def start(self):
        pass

    def pause(self):
        pass

    def stop(self):
        pass

    def refresh_connection(self):
        pass

    def open_logs(self):
        pass

    def move_block(self, path, delta):
        pass

    def hide_block(self, path):
        pass

    def restore_block(self, path):
        pass

    def edit_text(self, path, current):
        pass

    def toggle_editing(self):
        pass

    def resize_hero(self, path, ratio):
        pass

    def set_captain(self, name):
        pass

    def toggle_capture(self):
        pass

    def toggle_run(self):
        pass

    def set_skill(self, key, value):
        pass

    def set_quick_match(self, enabled):
        pass

    def set_auto_training(self, enabled):
        pass

    def start_auto_training(self):
        pass

    def stop_auto_training(self):
        pass

    def pause_current(self):
        pass

    def stop_current(self):
        pass


class _SplitHandle(QFrame):
    """hero 中间那条线：编辑态可以左右拖，改右栏宽度。"""

    moved = Signal(int)

    def __init__(self, target: QWidget, editing: bool, ratio_mode: bool = False):
        super().__init__()
        self.setObjectName("monitorSplitHandle")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("editing", "true" if editing else "false")
        self.target = target
        self.min_width = 120 if ratio_mode else 160
        self.max_width = 900 if ratio_mode else 520
        self._dragging = False
        self._start_x = 0
        self._start_w = 0
        if editing:
            self.setFixedWidth(7)
            self.setCursor(Qt.SplitHCursor)
            self.setToolTip("左右拖动，调整这一栏的宽度（松手自动保存）")
        else:
            self.setFixedWidth(1)

    def mousePressEvent(self, event):
        if self.width() <= 1:
            return
        self._dragging = True
        self._start_x = event.globalPosition().x()
        self._start_w = self.target.width()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return
        dx = event.globalPosition().x() - self._start_x
        width = int(max(self.min_width, min(self.max_width, self._start_w - dx)))
        self.target.setFixedWidth(width)

    def mouseReleaseEvent(self, event):
        if not self._dragging:
            return
        self._dragging = False
        self.moved.emit(self.target.width())


class _HeroSplit(QWidget):
    """主状态卡的左右分栏：按比例分配，窗口缩放时保持比例。"""

    def __init__(self, left: QWidget, handle: QFrame, right: QWidget, ratio: float = 0.5):
        super().__init__()
        self._handle = handle
        self._right = right
        self._ratio = max(0.2, min(0.8, float(ratio)))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(left, 1)
        lay.addWidget(handle)
        lay.addWidget(right, 0)

    def ratio(self) -> float:
        return self._ratio

    def avail(self) -> int:
        return max(1, self.width() - self._handle.width())

    def _apply(self):
        if self.width() < 240:
            return
        width = int(self.avail() * self._ratio)
        if self._right.width() != width:
            self._right.setFixedWidth(width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply()


class MonitorView(QWidget):
    """按 layout 描述生成卡片树，并在 update(data) 时刷新文本。"""

    def __init__(self, layout: dict, actions: MonitorActions | None = None,
                 editing: bool = False, captains: dict | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.layout_spec = layout or {}
        self.actions = actions or MonitorActions()
        self.editing = editing
        self.captains = captains or {}
        self._captain_check: CheckBox | None = None
        self._captain_combo: ComboBox | None = None
        self._btn_capture: QPushButton | None = None
        self._btn_run: QPushButton | None = None
        self._switch_quick: SwitchButton | None = None
        self._btn_train_start = None
        self._btn_train_stop = None
        self._btn_pause: QPushButton | None = None
        self._btn_stop: QPushButton | None = None
        self._score_value: QLabel | None = None
        self._skill_labels: dict = {}
        self._skill_gain: QLabel | None = None
        self._attr_labels: dict = {}
        self._last_captain = ""
        self._last_valid_index = 0
        self._syncing_captain = False
        self._callbacks: list = []
        self._bindings: list[tuple[QLabel, str, str | None]] = []
        self._timeline_box: QVBoxLayout | None = None
        self._timeline_spec: dict = {}
        self._stat_bars: dict[str, QProgressBar] = {}

        self.setObjectName("monitorRoot")
        self.vBox = QVBoxLayout(self)
        self.vBox.setContentsMargins(0, 0, 0, 0)
        self.vBox.setSpacing(12)
        self.vBox.setAlignment(Qt.AlignTop)

        self._build()

    # ---------- 构建 ----------

    def _build(self):
        for idx, section in enumerate(self.layout_spec.get("sections", [])):
            if section.get("_hidden"):
                continue
            kind = section.get("type")
            path = ("sections", idx)
            if kind == "connection":
                self._build_connection(section, path)
            elif kind == "hero":
                self._build_hero(section, path)
            elif kind == "cards":
                self._build_cards(section, path)
            elif kind == "captain":
                self._build_captain(section, path)
            elif kind == "timeline":
                self._build_timeline(section, path)
        if self.editing:
            self._build_hidden_bar()

    def _card(self, height: int | None = None) -> QFrame:
        frame = QFrame()
        frame.setObjectName("monitorCard")
        frame.setAttribute(Qt.WA_StyledBackground, True)
        if height:
            frame.setFixedHeight(int(height))
        return frame

    def _label(self, text: str = "", role: str = "", edit_path=None) -> QLabel:
        lb = QLabel(text)
        if role:
            lb.setProperty("mRole", role)
        if edit_path is not None and self.editing:
            lb.setProperty("mEditable", "true")
            lb.setCursor(Qt.IBeamCursor)
            lb.setAttribute(Qt.WA_Hover, True)
            lb.installEventFilter(self)
            lb._edit_path = edit_path
        return lb

    def _bind(self, label: QLabel, template: str, fallback: str | None = None):
        self._bindings.append((label, template, fallback))
        return label

    def _keep(self, callback):
        """保住槽的强引用。

        PySide6 对外部对象的绑定方法 / lambda 只持弱引用，
        对象被回收后按钮点了没反应（实测：暂停/停止按钮点了不动）。
        """
        self._callbacks.append(callback)
        return callback

    # ---- 槽：全部走 MonitorView 自己的方法，Qt 才会可靠地保持连接 ----


    def _button(self, text, handler, height=28, object_name="monitorGhost",
                tooltip=None, min_width=None):
        btn = QPushButton(text)
        btn.setObjectName(object_name)
        btn.setFixedHeight(height)
        if min_width:
            btn.setMinimumWidth(min_width)
        if tooltip:
            btn.setToolTip(tooltip)
        # 不用 clicked 信号：这个环境下绑定方法连不上（点了没反应），
        # 统一走 eventFilter 的鼠标抬起事件。
        btn._click_handler = self._keep(lambda h=handler: h())
        btn.installEventFilter(self)
        return btn

    @Slot()
    def _slot_capture(self):
        self.actions.toggle_capture()

    @Slot()
    def _slot_run(self):
        self.actions.toggle_run()

    @Slot()
    def _slot_refresh(self):
        self.actions.refresh_connection()

    @Slot()
    def _slot_logs(self):
        self.actions.open_logs()

    @Slot()
    def _slot_edit(self):
        self.actions.toggle_editing()

    @Slot()
    def _slot_start(self):
        self.actions.start()

    @Slot()
    def _slot_task_pause(self):
        self.actions.pause()

    @Slot()
    def _slot_task_stop(self):
        self.actions.stop()

    @Slot()
    def _slot_pause(self):
        self.actions.pause_current()

    @Slot()
    def _slot_stop(self):
        self.actions.stop_current()

    def _make_primary(self, text, handler, height=30, icon=None):
        btn = PrimaryPushButton(icon or FluentIcon.PLAY, text)
        btn.setFixedHeight(height)
        btn._click_handler = self._keep(lambda h=handler: h())
        btn.installEventFilter(self)
        return btn

    def _make_plain(self, text, handler, height=30):
        btn = PushButton(FluentIcon.PLAY, text)
        btn.setFixedHeight(height)
        btn._click_handler = self._keep(lambda h=handler: h())
        btn.installEventFilter(self)
        return btn

    def _attr_icon(self, name: str, width: int = 18):
        """属性小图标：assets/ui/attrs/<属性>.png；没有就返回 None（只显示文字）.

        素材是游戏里的原始比例（65x52，不是正方形），按宽度等比缩放。
        """
        path = get_relative_path(os.path.join(ATTR_ICON_DIR, f"{name}.png"))
        if not os.path.exists(path):
            return None
        pix = QPixmap(path)
        if pix.isNull():
            return None
        height = max(1, round(width * pix.height() / max(1, pix.width())))
        label = QLabel()
        label.setPixmap(pix.scaled(width, height, Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation))
        label.setFixedSize(width, height)
        return label

    @Slot(str, int)
    def _slot_skill(self, key, value):
        self.actions.set_skill(key, value)

    @Slot()
    def _slot_train_start(self):
        self.actions.start_auto_training()

    @Slot()
    def _slot_train_stop(self):
        self.actions.stop_auto_training()

    @Slot(bool)
    def _slot_quick(self, checked):
        self.actions.set_quick_match(bool(checked))

    @Slot(bool)
    def _slot_train(self, checked):
        self.actions.set_auto_training(bool(checked))

    def _slot_move(self, path, delta):
        self.actions.move_block(path, delta)

    def _slot_hide(self, path):
        self.actions.hide_block(path)

    def _slot_restore(self, path):
        self.actions.restore_block(path)

    def _slot_resize(self, path, split, width):
        self.actions.resize_hero(path, width / max(1, split.avail()))

    def _block_wrapper(self, card: QWidget, path, can_hide: bool = True) -> QWidget:
        """编辑态给区块套一层工具条；非编辑态原样返回。"""
        if not self.editing:
            return card
        wrapper = QWidget()
        wrapper.setObjectName("monitorBlock")
        lay = QVBoxLayout(wrapper)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        bar = QWidget()
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(0, 0, 0, 0)
        bar_lay.setSpacing(4)
        bar_lay.addStretch(1)
        buttons = [("↑", "上移", partial(self._slot_move, path, -1)),
                   ("↓", "下移", partial(self._slot_move, path, 1))]
        if can_hide:
            buttons.append(("✕", "隐藏", partial(self._slot_hide, path)))
        for glyph, tip, fn in buttons:
            btn = self._button(glyph, fn, height=22, object_name="monitorEditBtn", tooltip=tip)
            btn.setFixedSize(22, 22)
            btn.setCursor(Qt.PointingHandCursor)
            bar_lay.addWidget(btn)
        lay.addWidget(bar)
        lay.addWidget(card)
        return wrapper

    def _build_connection(self, spec: dict, path):
        card = self._card(spec.get("height", 54))
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(10)

        halo = QFrame()
        halo.setObjectName("monitorHalo")
        halo.setFixedSize(16, 16)
        halo_lay = QGridLayout(halo)
        halo_lay.setContentsMargins(4, 4, 4, 4)
        dot = QFrame()
        dot.setObjectName("monitorDot")
        dot.setFixedSize(8, 8)
        dot.setProperty("state", "idle")
        self._dot = dot
        halo_lay.addWidget(dot, 0, 0)
        lay.addWidget(halo)

        name = self._label("", "connName", path + ("title",))
        lay.addWidget(self._bind(name, spec.get("title", "{device_title}")))
        meta = self._label("", "connMeta", path + ("meta",))
        lay.addWidget(self._bind(meta, spec.get("meta", "{device_meta}")))

        lay.addStretch(1)

        if spec.get("show_capture", False):
            self._btn_capture = self._button(
                spec.get("capture_text", "开始截图"), self._slot_capture,
                tooltip="等同「截图方式」页的截图按钮")
            lay.addWidget(self._btn_capture)

        if spec.get("show_run", True):
            self._btn_run = self._button(
                spec.get("run_text", "开始"), self._slot_run,
                object_name="monitorPrimary", min_width=78,
                tooltip="开始 / 暂停（等同开始按钮）")
            lay.addWidget(self._btn_run)

        for text, slot in (("重新检测", self._slot_refresh),):
            lay.addWidget(self._button(text, slot))

        self.vBox.addWidget(self._block_wrapper(card, path, can_hide=False))

    def _build_hero(self, spec: dict, path):
        card = self._card(spec.get("height", 176))
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(20)

        left = QVBoxLayout()
        left.setSpacing(6)

        head_row = QHBoxLayout()
        head_row.setSpacing(10)
        head_row.addWidget(self._label(spec.get("kicker", "当前进度"), "kicker",
                                       path + ("kicker",)))
        head_row.addWidget(self._bind(
            self._label("", "heroText", path + ("title",)),
            spec.get("title", "第 {week} 周 {day}"), spec.get("fallback")))
        head_row.addStretch(1)
        left.addLayout(head_row)

        stats = QHBoxLayout()
        stats.setSpacing(28)
        for sidx, stat in enumerate(spec.get("stats", [])):
            base = path + ("stats", sidx)
            box = QVBoxLayout()
            box.setSpacing(3)
            box.addWidget(self._label(stat.get("label", ""), "statKey", base + ("label",)))

            value_row = QHBoxLayout()
            value_row.setSpacing(2)
            value = self._label("", "statValue", base + ("value",))
            value_row.addWidget(self._bind(value, stat.get("value", "")))
            if stat.get("suffix"):
                suffix = self._label("", "statSuffix", base + ("suffix",))
                value_row.addWidget(self._bind(suffix, stat["suffix"]), 0, Qt.AlignBottom)
            box.addLayout(value_row)

            if stat.get("bar"):
                bar = QProgressBar()
                bar.setRange(0, 100)
                bar.setValue(0)
                bar.setTextVisible(False)
                bar.setFixedHeight(6)
                bar.setFixedWidth(int(stat.get("bar_width", 150)))
                bar.setObjectName("monitorBar")
                self._stat_bars[stat.get("bar_key", stat.get("label", "bar"))] = bar
                box.addWidget(bar)
            box.addStretch(1)
            stats.addLayout(box)
        stats.addStretch(1)
        left.addLayout(stats)
        left.addStretch(1)

        left_box = QWidget()
        left_box.setLayout(left)
        left_box.setMinimumWidth(int(spec.get("left_min_width", 280)))

        right = QVBoxLayout()
        right.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(self._label(spec.get("score_label", "分数预测"), "kicker"))
        self._score_value = self._label("", "scoreValue")
        head.addWidget(self._score_value)
        head.addSpacing(16)
        for key, text in (("color", "彩"), ("gold", "金"), ("white", "白")):
            head.addWidget(self._label(text, "skillTag"))
            value = self._label("", "skillValue")
            head.addWidget(value)
            self._skill_labels[key] = value
            head.addSpacing(4)
        self._skill_gain = self._label("", "skillGain")
        head.addWidget(self._skill_gain)
        head.addStretch(1)
        right.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(4)
        for idx, name in enumerate(spec.get("attrs") or DEFAULT_ATTRS):
            cell = QHBoxLayout()
            cell.setSpacing(6)
            icon = self._attr_icon(name)
            if icon is not None:
                cell.addWidget(icon, 0, Qt.AlignVCenter)
            cell.addWidget(self._label(name, "attrName"))
            cell.addStretch(1)
            value = self._label("", "attrValue")
            cell.addWidget(value)
            self._attr_labels[name] = value
            holder = QWidget()
            holder.setLayout(cell)
            # 和游戏一致：列优先（左列 投射/弹跳/运控，右列 体能/防守/意识）
            grid.addWidget(holder, idx % 3, idx // 3)
        right.addLayout(grid)
        right.addStretch(1)

        right_box = QWidget()
        right_box.setLayout(right)
        right_box.setFixedWidth(int(spec.get("right_width", 232)))

        handle = _SplitHandle(right_box, self.editing, ratio_mode=True)
        split = _HeroSplit(left_box, handle, right_box,
                           float(spec.get("right_ratio", 0.5) or 0.5))
        handle.moved.connect(self._keep(partial(self._slot_resize, path, split)))
        lay.addWidget(split, 1)

        self.vBox.addWidget(self._block_wrapper(card, path))

    def _build_captain(self, spec: dict, path):
        """队长选择：勾选框 + 按流派分组的下拉。取消勾选 = 无队长。"""
        card = self._card(spec.get("height", 54))
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(10)

        self._captain_check = CheckBox(spec.get("label", "指定队长"))
        lay.addWidget(self._captain_check)

        combo = ComboBox()
        combo.setMinimumWidth(int(spec.get("combo_width", 220)))
        for style, names in self.captains.items():
            combo.addItem(f"── {style} ──", userData=GROUP)
            for name in names:
                combo.addItem(name, userData=name)
        self._captain_combo = combo
        lay.addWidget(combo)

        self._captain_check.setToolTip(
            spec.get("hint", "取消勾选 = 不接开局，只跑已有局"))

        if spec.get("show_auto_training", True):
            # 直接搬 ok-script 任务卡那个「▶ 开始」按钮：原生 PrimaryPushButton。
            # 开/关两个实例切换显示，保证配色、hover、主题跟随都和框架一致。
            # 和任务卡完全一致：没运行时是蓝色「▶ 开始」，运行中是蓝色「■ 停止」
            self._btn_train_start = self._make_primary(
                spec.get("start_text", "开始"), self._slot_train_start,
                icon=FluentIcon.PLAY)
            self._btn_train_stop = self._make_primary(
                spec.get("stop_text", "停止"), self._slot_train_stop,
                icon=OKIcon.STOP)
            self._btn_train_stop.setVisible(False)
            lay.addWidget(self._btn_train_start)
            lay.addWidget(self._btn_train_stop)

        lay.addStretch(1)

        if spec.get("show_quick_match", True):
            lay.addWidget(self._label(spec.get("quick_label", "快速比赛"), "rowLeft"))
            self._switch_quick = SwitchButton()
            self._switch_quick.setOnText("开")
            self._switch_quick.setOffText("关")
            self._switch_quick.checkedChanged.connect(self._slot_quick)
            lay.addWidget(self._switch_quick)

        edit_btn = self._button("✎", self._slot_edit, object_name="monitorEditEntry",
                                tooltip="编辑布局：上下移动、隐藏卡片、双击改文字")
        edit_btn.setFixedSize(28, 28)
        lay.addWidget(edit_btn)

        combo.currentIndexChanged.connect(self._on_captain_index)
        self._captain_check.toggled.connect(self._on_captain_check)

        if not self.captains:
            combo.setEnabled(False)
            self._captain_check.setEnabled(False)

        self.vBox.addWidget(self._block_wrapper(card, path, can_hide=False))

    # ---------- 队长控件 ----------

    def _first_captain(self) -> str:
        for names in self.captains.values():
            if names:
                return names[0]
        return ""

    def _index_of(self, name: str) -> int:
        combo = self._captain_combo
        if combo is None or not name:
            return -1
        for i in range(len(combo.items)):
            if combo.itemData(i) == name:
                return i
        # 2026-10-31：名字写法可能和角色表不一样（游戏里读到「亚理纱贾斯敏」、
        # 表里写「亚理纱·贾斯敏」），再按归一化后的包含关系找一遍，
        # 否则下拉框会空着/跳回第一个，看着像「没匹配上」。
        try:
            from src.tasks.ui.Defs import name_matches
        except Exception:  # noqa: BLE001
            return -1
        for i in range(len(combo.items)):
            data = combo.itemData(i)
            if isinstance(data, str) and data != GROUP and name_matches(name, data):
                return i
        return -1

    def _select_index(self, index: int):
        if self._captain_combo is None or index < 0:
            return
        self._syncing_captain = True
        try:
            self._captain_combo.setCurrentIndex(index)
        finally:
            self._syncing_captain = False

    def _on_captain_check(self, checked: bool):
        if self._syncing_captain:
            return
        combo = self._captain_combo
        if not checked:
            if combo is not None:
                combo.setEnabled(False)
            self.actions.set_captain("")
            return
        if combo is not None:
            combo.setEnabled(True)
        name = self._last_captain or self._first_captain()
        if name:
            index = self._index_of(name)
            if index >= 0:
                self._last_valid_index = index
            self._select_index(index)
            self.actions.set_captain(name)

    def _on_captain_index(self, index: int):
        combo = self._captain_combo
        if self._syncing_captain or combo is None:
            return
        data = combo.itemData(index)
        if data == GROUP:
            # 分组标题不可选：立刻退回上一个真实角色
            self._select_index(self._last_valid_index)
            return
        if data:
            self._last_captain = str(data)
            self._last_valid_index = index
            self.actions.set_captain(str(data))

    def _sync_captain(self, name: str):
        combo, check = self._captain_combo, self._captain_check
        if combo is None or check is None:
            return
        if combo.dropMenu is not None and combo.dropMenu.isVisible():
            return          # 用户正在下拉，别抢状态
        checked = bool(name)
        self._syncing_captain = True
        try:
            if check.isChecked() != checked:
                check.setChecked(checked)
            index = self._index_of(name)
            if index >= 0:
                self._last_captain = name
                self._last_valid_index = index
                if combo.currentIndex() != index:
                    combo.setCurrentIndex(index)
            combo.setEnabled(checked)
        finally:
            self._syncing_captain = False

    def _build_cards(self, spec: dict, path):
        row = QHBoxLayout()
        row.setSpacing(12)
        for jdx, item in enumerate(spec.get("items", [])):
            if item.get("_hidden"):
                continue
            base = path + ("items", jdx)
            card = self._card(spec.get("height", 126))
            lay = QVBoxLayout(card)
            lay.setContentsMargins(16, 14, 16, 10)
            lay.setSpacing(0)
            lay.addWidget(self._label(item.get("title", ""), "cardTitle",
                                      base + ("title",)))
            lay.addSpacing(4)
            for kdx, row_spec in enumerate(item.get("rows", [])):
                row_frame = QFrame()
                row_frame.setObjectName("monitorRow")
                row_lay = QHBoxLayout(row_frame)
                row_lay.setContentsMargins(0, 5, 0, 5)
                row_lay.setSpacing(8)
                row_lay.addWidget(self._label(row_spec[0], "rowLeft",
                                              base + ("rows", kdx, 0)))
                row_lay.addStretch(1)
                right = self._label("", "rowRight", base + ("rows", kdx, 1))
                if len(row_spec) > 1:
                    row_lay.addWidget(self._bind(right, row_spec[1]))
                else:
                    row_lay.addWidget(right)
                lay.addWidget(row_frame)
            row.addWidget(self._block_wrapper(card, base), 1)
        self.vBox.addLayout(row)

    def _build_timeline(self, spec: dict, path):
        card = self._card()
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(0)
        lay.addWidget(self._label(spec.get("title", "最近动作"), "cardTitle",
                                  path + ("title",)))
        lay.addSpacing(6)
        box = QVBoxLayout()
        box.setSpacing(7)
        lay.addLayout(box)
        lay.addStretch(1)
        self._timeline_box = box
        self._timeline_spec = spec
        self.vBox.addWidget(self._block_wrapper(card, path), 1)

    def _build_hidden_bar(self):
        sections = self.layout_spec.get("sections", [])
        hidden = []
        for idx, section in enumerate(sections):
            if section.get("_hidden"):
                hidden.append((("sections", idx), _block_name(section)))
            for jdx, item in enumerate(section.get("items", []) or []):
                if item.get("_hidden"):
                    hidden.append((("sections", idx, "items", jdx), _block_name(item)))
        if not hidden:
            return
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self._label("已隐藏：", "hiddenHint"))
        for path, name in hidden:
            btn = self._button(f"＋ {name}", partial(self._slot_restore, path),
                               height=24, object_name="monitorRestore")
            btn.setCursor(Qt.PointingHandCursor)
            lay.addWidget(btn)
        lay.addStretch(1)
        self.vBox.addWidget(bar)

    # ---------- 刷新 ----------

    def update_data(self, data: dict):
        for label, template, fallback in self._bindings:
            text, complete = _render(template, data)
            label.setText(text if complete or fallback is None else fallback)

        for key, bar in self._stat_bars.items():
            raw = data.get(key)
            try:
                bar.setValue(max(0, min(100, int(round(float(raw))))))
            except (TypeError, ValueError):
                bar.setValue(0)

        state = data.get("state", "idle")
        if hasattr(self, "_dot"):
            self._dot.setProperty("state", state)
            self._dot.style().unpolish(self._dot)
            self._dot.style().polish(self._dot)

        if self._timeline_box is not None:
            self._fill_timeline(data.get("timeline") or [])

        if self._captain_check is not None:
            self._sync_captain(data.get("captain") or "")

        self._sync_run_button(data)
        self._sync_switches(data)
        self._sync_score(data)

    def _sync_score(self, data: dict):
        if self._score_value is not None:
            score = data.get("score")
            self._score_value.setText(str(score) if score is not None else MISSING)
        attrs = data.get("attrs") or {}
        for name, label in self._attr_labels.items():
            value = attrs.get(name)
            label.setText(str(value) if value is not None else MISSING)
        for key, label in self._skill_labels.items():
            value = data.get(f"skill_{key}")
            label.setText(str(value) if value is not None else MISSING)
        if self._skill_gain is not None:
            gained = int(data.get("skill_gained") or 0)
            self._skill_gain.setText(f"本局 +{gained}" if gained else "")
            self._skill_gain.setToolTip(
                f"本局三选一拿到的技能数；上一次：{data.get('skill_last') or '—'}")

    def _sync_run_button(self, data: dict):
        if self._btn_run is None:
            return
        text = "暂停" if data.get("executor_running") else "开始"
        if self._btn_run.text() != text:
            self._btn_run.setText(text)

    def _sync_switches(self, data: dict):
        if self._btn_train_start is not None and "training_enabled" in data:
            on = bool(data.get("training_enabled"))
            self._btn_train_start.setVisible(not on)
            self._btn_train_stop.setVisible(on)
        for key, switch in (("quick_enabled", self._switch_quick),
                            ("training_enabled", None)):
            if switch is None or key not in data:
                continue
            value = bool(data.get(key))
            if switch.isChecked() != value:
                switch.blockSignals(True)
                switch.setChecked(value)
                switch.blockSignals(False)

    def _fill_timeline(self, events: list):
        box = self._timeline_box
        while box.count():
            item = box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        limit = int(self._timeline_spec.get("max_rows", 8))
        for ev in events[:limit]:
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(12)
            time_lb = self._label(str(ev.get("time", "")), "tlTime")
            time_lb.setFixedWidth(int(self._timeline_spec.get("time_width", 62)))
            lay.addWidget(time_lb)
            ev_lb = self._label(str(ev.get("event", "")), "tlEvent")
            ev_lb.setFixedWidth(int(self._timeline_spec.get("event_width", 80)))
            lay.addWidget(ev_lb)
            detail = self._label(str(ev.get("detail", "")), "tlDetail")
            lay.addWidget(detail, 1)
            result = self._label(str(ev.get("result", "")), "tlResult")
            lay.addWidget(result)
            box.addWidget(row)

    # ---------- 双击编辑 ----------
    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonRelease:
            handler = getattr(obj, "_click_handler", None)
            if handler is not None and event.button() == Qt.LeftButton:
                handler()
                return True
        if event.type() == QEvent.MouseButtonDblClick and self.editing:
            path = getattr(obj, "_edit_path", None)
            if path is not None:
                self.actions.edit_text(path, obj.text())
                return True
        return super().eventFilter(obj, event)


def _block_name(block: dict) -> str:
    title = str(block.get("title") or "")
    # 带 {字段} 的标题是模板，不能当名字用（会变成「第」这种半截文案）
    if title and "{" not in title:
        return title
    mapping = {"connection": "连接状态", "hero": "主状态", "cards": "摘要卡",
               "timeline": "最近动作"}
    return block.get("id") or mapping.get(block.get("type"), "区块")


def _render(template: str, data: dict) -> tuple[str, bool]:
    """把 `{key}` 换成值；返回 (文本, 是否全部有值)。"""
    if not template:
        return "", True
    complete = True

    def sub(m):
        nonlocal complete
        value = data.get(m.group(1))
        if value in (None, ""):
            complete = False
            return MISSING
        return str(value)

    return PLACEHOLDER.sub(sub, template), complete

