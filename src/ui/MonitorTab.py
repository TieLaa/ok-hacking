"""监控页：把训练任务的实时状态放到一屏里。

职责边界：
- 本文件只做「取数据 + 定时刷新 + 热重载」，界面结构全在 monitor_view.py；
- 布局来自 configs/monitor_layout.json，样式来自 assets/ui/monitor.*.qss，
  两者都在运行时监听 mtime，改完存盘即生效，不用重启；
- 不启动/不干扰任务逻辑，只在用户点按钮时调用框架已有的 executor 接口。
"""

from __future__ import annotations

import json
import os
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget
from qfluentwidgets import FluentIcon, isDarkTheme

from ok import Logger, og
from ok.ui.qt.widget.CustomTab import CustomTab
from ok.util.file import get_relative_path

from src.ui.monitor_view import MISSING, MonitorActions, MonitorView

logger = Logger.get_logger(__name__)

LAYOUT_PATH = "configs/monitor_layout.json"
QSS_DIR = os.path.join("assets", "ui")
# 启动后自动切到「监控」页（想回到「截图方式」页就改成 False）
START_ON_MONITOR = True
START_RETRY_MS = 100
START_RETRY_MAX = 100  # 最多等 10 秒，主窗口还没显示就不抢了
# 技能品质权重（docs/score-model/README.md：彩 75 / 金 50 / 白 25）
SKILL_WEIGHTS = {"color": 75, "gold": 50, "white": 25}
SKILL_KEYS = {"color": "技能-彩色", "gold": "技能-金色", "white": "技能-白色"}
# 六维必须**齐 6 项**才算「分数预测」（和 training/parsers.ATTR_KEYS 一致）。
# 面板被说明气泡挡住 / 这一帧漏读一项时，拿 4~5 项求和会给出偏低的分，
# 用户看到的就是「分数自己乱跳」——宁可不显示，也不显示错的。
ATTR_KEYS = 6

DEFAULT_LAYOUT = {
    "version": 1,
    "sections": [
        {"type": "connection", "height": 54,
         "title": "{device_title}", "meta": "{device_meta}",
         "show_run": True, "run_text": "启动", "stop_text": "停止"},
        {"type": "captain", "height": 54,
         "label": "指定队长",
         "hint": "取消勾选 = 不接开局，只跑已有局",
         "show_quick_match": True, "show_auto_training": True,
         "start_text": "开始", "pause_text": "暂停", "starting_text": "启动中…"},
        {"type": "hero", "height": 204,
         "kicker": "当前进度",
         "title": "第 {week} 周 {day}",
         "fallback": "等待开始",
         "right_ratio": 0.5,
         "stats": [
             {"label": "专注", "value": "{stamina}", "suffix": "/{stamina_max}",
              "bar": True, "bar_key": "stamina_pct", "bar_width": 150},
             {"label": "零用钱", "value": "{coin}"},
             {"label": "流派", "value": "{style}"},
             {"label": "已运行", "value": "{uptime}"},
         ]},
        {"type": "cards", "height": 126, "items": [
            {"id": "today", "title": "今日计划", "rows": [
                ["比赛倒计时", "{match_countdown}"],
                ["周末去处", "{weekend_plan}"],
                ["野餐阈值", "精力 < {picnic_threshold}"],
            ]},
            {"id": "shop", "title": "商店", "rows": [
                ["商店购买策略", "{buy_strategy}"],
                ["今日已逛", "{shop_today}"],
            ]},
            {"id": "risk", "title": "风险", "rows": [
                ["当前失败率", "{fail_rate}"],
                ["失败率上限", "{fail_limit}"],
                ["卡住干预", "{stuck}"],
            ]},
        ]},
        {"type": "timeline", "title": "最近动作", "max_rows": 8,
         "time_width": 62, "event_width": 80},
    ],
}


class _Actions(MonitorActions):
    def __init__(self, tab: "MonitorTab"):
        self.tab = tab

    def refresh_connection(self):
        self.tab.refresh()

    def open_logs(self):
        self.tab.open_logs()

    def move_block(self, path, delta):
        self.tab.move_block(path, delta)

    def hide_block(self, path):
        self.tab.hide_block(path)

    def restore_block(self, path):
        self.tab.restore_block(path)

    def edit_text(self, path, current):
        self.tab.edit_text(path, current)

    def toggle_editing(self):
        self.tab.toggle_editing()

    def resize_hero(self, path, right_width):
        self.tab.resize_hero(path, right_width)

    def set_captain(self, name):
        self.tab.set_captain(name)

    def toggle_capture(self):
        self.tab.toggle_capture()

    def toggle_connection(self):
        self.tab.toggle_connection()

    def set_skill(self, key, value):
        self.tab.set_skill(key, value)

    def set_quick_match(self, enabled):
        self.tab.set_quick_match(enabled)

    def set_score_stop(self, enabled):
        self.tab.set_score_stop(enabled)

    def set_score_target(self, target):
        self.tab.set_score_target(target)

    def set_stamina_allowed(self, allowed):
        self.tab.set_stamina_allowed(allowed)

    def toggle_auto_training(self):
        self.tab.toggle_auto_training()

    def hard_stop_training(self):
        self.tab.hard_stop_training()


class MonitorTab(CustomTab):
    """左侧导航的「监控」页。"""

    @property
    def name(self):
        return "监控"

    @property
    def add_after_default_tabs(self):
        # False = 插在「截图方式」之后、任务/触发之前；启动时还会自动切过来（见 _select_on_startup）。
        return False

    def __init__(self):
        super().__init__()
        self.icon = FluentIcon.SPEED_HIGH
        self.setObjectName("MonitorTabView")
        self._layout_data = self._load_layout()
        self._layout_mtime = self._mtime(self._layout_path())
        self._qss_mtime = 0.0
        self._view: MonitorView | None = None
        self._editing = False

        self._build_view()
        self._apply_style()
        self._bind_theme()

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._started_selected = False
        if START_ON_MONITOR:
            QTimer.singleShot(0, self._select_on_startup)

    # ---------- 启动默认页 ----------

    def _select_on_startup(self, tries: int = 0):
        """启动后把左侧导航切到「监控」页。

        构造期间主窗口还没赋给 og.main_window、也还没显示，所以轮询等到
        showEvent 之后（win.shown）再切，避免和导航栏的初始化抢。
        """
        if self._started_selected:
            return
        win = getattr(og, "main_window", None)
        if win is None or not getattr(win, "shown", False):
            if tries < START_RETRY_MAX:
                QTimer.singleShot(START_RETRY_MS, lambda: self._select_on_startup(tries + 1))
            return
        self._started_selected = True
        try:
            win.switchTo(self)
            logger.info("启动默认页：监控")
        except Exception as e:  # noqa: BLE001
            logger.error(f"切换到监控页失败: {e}")

    # ---------- 布局 / 样式文件 ----------

    @staticmethod
    def _layout_path() -> str:
        return get_relative_path(LAYOUT_PATH)

    @staticmethod
    def _qss_path(dark: bool) -> str:
        name = "monitor.dark.qss" if dark else "monitor.light.qss"
        return get_relative_path(os.path.join(QSS_DIR, name))

    @staticmethod
    def _mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def _load_layout(self) -> dict:
        path = self._layout_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            self._write_default_layout(path)
            return DEFAULT_LAYOUT
        except Exception as e:  # noqa: BLE001
            logger.error(f"monitor_layout.json 解析失败，用内置默认布局: {e}")
            return DEFAULT_LAYOUT

    def _write_default_layout(self, path: str):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_LAYOUT, f, ensure_ascii=False, indent=2)
            logger.info(f"已生成默认布局文件 {path}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"写默认布局失败 {path}: {e}")

    def _build_view(self):
        if self._view is not None:
            self.vBoxLayout.removeWidget(self._view)
            self._view.deleteLater()
        task = self._task()
        config = dict(getattr(task, "config", {}) or {}) if task else {}
        # 当前队长要传进去：它的流派就算被 `CAPTAIN_DROPDOWN_STYLES` 隐藏了，
        # 下拉里也得留着这个人，否则回填不到 / 一勾就被换成训练流。
        self._view = MonitorView(self._layout_data, _Actions(self), editing=self._editing,
                                  captains=self._captains(
                                      (config.get("指定队长") or "").strip()))
        self.add_widget(self._view)
        self._apply_style()
        self._update_data()

    # ---------- 布局编辑 ----------

    def toggle_editing(self):
        self._editing = not self._editing
        self._build_view()
        logger.info(f"监控页编辑模式：{'开' if self._editing else '关'}")

    def move_block(self, path, delta):
        parent = _locate(self._layout_data, path[:-1])
        index = path[-1]
        target = index + delta
        if isinstance(parent, list) and 0 <= target < len(parent):
            parent[index], parent[target] = parent[target], parent[index]
            self._persist()

    def hide_block(self, path):
        block = _locate(self._layout_data, path)
        if isinstance(block, dict):
            block["_hidden"] = True
            self._persist()

    def restore_block(self, path):
        block = _locate(self._layout_data, path)
        if isinstance(block, dict):
            block.pop("_hidden", None)
            self._persist()

    def resize_hero(self, path, ratio):
        """hero 分割线拖动后写回左右比例（0.2~0.8）。"""
        block = _locate(self._layout_data, path)
        if not isinstance(block, dict):
            return
        value = max(0.2, min(0.8, round(float(ratio), 3)))
        if abs(float(block.get("right_ratio", 0.5) or 0.5) - value) > 0.004:
            block["right_ratio"] = value
            block.pop("right_width", None)
            self._persist()

    def edit_text(self, path, current):
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "编辑文字", "内容（可用 {字段} 占位）：",
                                        text=str(current))
        if not ok or text == current:
            return
        parent = _locate(self._layout_data, path[:-1])
        if isinstance(parent, (list, dict)):
            parent[path[-1]] = text
            self._persist()

    def _persist(self):
        path = self._layout_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._layout_data, f, ensure_ascii=False, indent=2)
            # 先记账再重建，避免自己的写入被 mtime 监听当成外部修改
            self._layout_mtime = self._mtime(path)
        except Exception as e:  # noqa: BLE001
            logger.error(f"保存布局失败 {path}: {e}")
            return
        self._build_view()

    def _apply_style(self):
        path = self._qss_path(isDarkTheme())
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.setStyleSheet(f.read())
            self._qss_mtime = self._mtime(path)
        except OSError:
            logger.warning(f"样式文件不存在，使用框架默认样式: {path}")

    def _bind_theme(self):
        from qfluentwidgets import qconfig
        qconfig.themeChanged.connect(lambda *_: self._apply_style())

    # ---------- 刷新 ----------

    def _tick(self):
        if self._layout_path_changed():
            self._layout_data = self._load_layout()
            self._layout_mtime = self._mtime(self._layout_path())
            self._build_view()
            self._apply_style()
            logger.info("检测到布局文件变更，已重建监控页")
        elif self._mtime(self._qss_path(isDarkTheme())) != self._qss_mtime:
            self._apply_style()
            logger.info("检测到样式文件变更，已重新加载")
        self._update_data()

    def _layout_path_changed(self) -> bool:
        return self._mtime(self._layout_path()) != self._layout_mtime

    def _update_data(self):
        if self._view is None:
            return
        self._view.update_data(self._collect())

    # ---------- 数据 ----------

    def _task(self):
        try:
            from src.tasks.TrainingTask import TrainingTask
            return self.executor.get_task_by_class(TrainingTask) if self.executor else None
        except Exception:  # noqa: BLE001
            return None

    def _collect(self) -> dict:
        task = self._task()
        info = dict(getattr(task, "info", {}) or {}) if task else {}
        config = dict(getattr(task, "config", {}) or {}) if task else {}
        attrs = dict(info.get("attrs") or {})
        # 配置里是开局已有的技能数；info["skill_gained"] 是本局三选一拿到的，相加才是当前总数
        gained = info.get("skill_gained") if isinstance(info.get("skill_gained"), dict) else {}
        skills = {k: int(config.get(key, 0) or 0) + int(gained.get(k, 0) or 0)
                  for k, key in SKILL_KEYS.items()}
        # 评分 = 六维总和 + 75×彩 + 50×金 + 25×白（每角色开局恒 1 彩 + 1 白，见 docs/score-model）
        # **六维不齐就不算**（2026-11-04）：面板被挡/OCR 漏一项时，拿 4 项求和会给出
        # 一个偏低的分（用户看到「突然 9k / 1w4」的另一半原因），不如显示「—」。
        total_attr = sum(v for v in attrs.values() if isinstance(v, (int, float)))
        score = None
        if len(attrs) >= ATTR_KEYS and total_attr:
            score = int(round(total_attr)) + sum(
                SKILL_WEIGHTS[k] * v for k, v in skills.items())

        stamina = info.get("stamina")
        stamina_max = info.get("stamina_max")
        pct = 0
        if isinstance(stamina, (int, float)) and isinstance(stamina_max, (int, float)) and stamina_max:
            pct = round(stamina / stamina_max * 100)

        data = {
            "state": self._state(task),
            # 留 None 而不是「—」，这样 hero 标题能命中 fallback（等待开始）
            "week": info.get("week"),
            "day": info.get("day"),
            "stamina": stamina if stamina is not None else MISSING,
            "stamina_max": stamina_max if stamina_max is not None else MISSING,
            "stamina_pct": pct,
            "coin": info.get("coin") if info.get("coin") is not None else MISSING,
            "style": info.get("style") or self._style_from_config(config),
            "action": info.get("action") or "空闲",
            "uptime": _fmt_uptime(task),
            "task_hint": info.get("task_hint") or "养成-训练循环",
            "match_countdown": info.get("match_countdown") or MISSING,
            "weekend_plan": info.get("weekend_plan") or MISSING,
            "picnic_threshold": config.get("周末野餐阈值", MISSING),
            "buy_strategy": config.get("商店购买策略", MISSING),
            "shop_today": info.get("shop_today") or "否",
            "fail_rate": info.get("fail_rate") or info.get("当前失败率") or MISSING,
            "captain": (config.get("指定队长") or "").strip(),
            # 第一栏总开关的状态：**会话在跑没有**（截帧通道活着 **且** 执行器
            # 没暂停，见 `_connected()`）。只看 capture_method 会在启动时就为真
            # —— 框架启动时会自动刷新设备并建 WGC 截图会话，按钮一进页面
            # 就显示【停止】，看着像「默认已经开始了」。
            "connected": self._connected(),
            # 第二栏按钮的状态：自动训练任务在跑 / 在队列里等
            "training_running": self._running_of(task),
            # 第二栏按钮文案要区分「真在跑」和「只是排上队了」：
            # 只有 running 才算在跑；enabled 但没 running = 还没开始。
            "training_active": self._active_of(task),
            # 任务是否处于框架的 task.pause() 挂起态（enabled=True + paused=True）：
            # 暂停态下第二栏按钮显示【开始】（点击 = unpause 恢复），
            # 对齐框架 TaskCard.update_buttons 的「enabled+paused → Start(Resume)」。
            "training_paused": (bool(getattr(task, "enabled", False))
                                and bool(getattr(task, "paused", False))) if task else False,
            # 正在接入游戏（第二栏【开始】触发的自动启动，或第一栏【启动】）：
            # 接入期间按钮显示【启动中…】，免得用户以为点了没反应。
            "connecting": self._starting(),
            "attrs": attrs,
            "score": score,
            "skill_color": skills["color"],
            "skill_gold": skills["gold"],
            "skill_white": skills["white"],
            "skill_gained": (gained.get("color", 0) + gained.get("gold", 0)
                             + gained.get("white", 0)),
            "skill_last": info.get("skill_last") or "",
            "quick_enabled": self._enabled_of(self._task_by_name("QuickMatchTask")),
            # 第二栏新增控件的状态（用户 2026-11-05）：「刷到目标分」= 队列参数
            # 在内存里挂着（2026-11-07 起队列不再走配置键）
            "score_stop": bool(getattr(task, "_score_stop_on", False)) if task else False,
            "score_target": self._queue_target_text(task, config),
            "stamina_allowed": bool(config.get("自动恢复精力")),
            "fail_limit": config.get("失败率上限(%)", MISSING),
            "stuck": info.get("stuck") or (
                f"3 秒无响应 / 日 {config.get('单日最大点击数', MISSING)} 次 · 未触发"),
            "timeline": info.get("timeline") or [],
            "device_title": "篮球少女：飞跃巅峰",
            "device_meta": self._device_meta(),
        }
        return data

    @staticmethod
    def _queue_target_text(task, config: dict) -> str:
        """取「当前队长」的目标分，给监控页输入框回填。

        2026-11-07 起队列是任务内存里的参数（`task._queue_spec`），
        配置键已废弃只在任务没起来时兜底读一次旧配置。
        """
        spec = (getattr(task, "_queue_spec", None) or "") if task else ""
        if not spec:
            spec = config.get("队长队列") or ""
        try:
            from src.tasks.training.queue import parse_queue
            items = parse_queue(spec)
        except Exception:  # noqa: BLE001
            return ""
        for item in items:
            if item.get("target"):
                return str(item["target"])
        return ""

    @staticmethod
    def _style_from_config(config: dict) -> str:
        """配置里的指定队长 → 流派；查不到就按训练流（和 flow.style 的兜底一致）。

        2026-11-03 改成走 `style_for_name`：错字别名（羽漱川湊 → 羽濑川湊）
        和「带稀有度前缀 / 丢间隔点」的归一化都只在那一处，别在这里再实现一遍。
        """
        name = (config.get("指定队长") or "").strip()
        if name:
            try:
                from src.tasks.training.parsers import TrainingParsersMixin
                style = TrainingParsersMixin.style_for_name(name)
                if style:
                    return style
            except Exception:  # noqa: BLE001
                pass
        return "训练流"

    def _state(self, task) -> str:
        if task is None:
            return "idle"
        if getattr(task, "running", False):
            return "running"
        if getattr(task, "enabled", False):
            return "paused" if getattr(task, "paused", False) else "queued"
        return "idle"

    @staticmethod
    def _connected() -> bool:
        """游戏会话在跑没有 —— 第一栏【启动/停止】和第二栏【开始】的唯一判据。

        **两个条件都要**（2026-11-02 第二次修正，用户口径「进去的时候默认就
        开始了」）：

        1. 截帧通道活着：`device_manager.capture_method` 在且 `connected()` 为真；
        2. 执行器没被暂停：`executor.paused` 为假。

        只满足第 1 条不够：框架**启动时就会自动刷新设备**
        （日志里能看到 `RefreshAdb ... windows_graphics:start_or_stop start WGC
        capture`），app 一打开 `capture_method` 就已经有对象了 —— 旧版只看它，
        于是第一栏一进页面就显示【停止】，看起来像「已经开始了」。
        执行器启动时是 `paused=True`，加上第 2 条才能把「截着图」和
        「真在跑」分开。

        **仍然不看任务状态**（第二栏）：第二栏点【暂停】只 `task.disable()`，
        不碰执行器，所以不会把第一栏顶回去（旧版的坑是 `task.unpause()` →
        `executor.start()`，那个调用已经删了）。
        """
        try:
            device_manager = getattr(og, "device_manager", None)
            if device_manager is None:
                return False
            capture = getattr(device_manager, "capture_method", None)
            if capture is None:
                return False
            connected = getattr(capture, "connected", None)
            if callable(connected) and not connected():
                return False
            executor = getattr(og, "executor", None)
            if executor is None:
                return False
            return not bool(getattr(executor, "paused", True))
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _active_of(task) -> bool:
        """任务是不是「已启动」（对齐框架 TaskCard.update_buttons 的判据）。

        框架口径：`task.enabled` 为真就显示 Stop/Pause 按钮（无论 paused 还是
        running）；只有未 enabled 才回到 Start。我们第二栏的【开始/暂停】
        现在用框架的 task.pause()/unpause()，「暂停态」也是 enabled=True ——
        所以这里的语义从「真在跑」放宽为「已启动（在跑或已暂停）」：
        - enabled=True（running 或 paused）→ 第二栏显示【暂停】、■ 可点；
        - enabled=False（未启动/已停止）→ 显示【开始】、■ 置灰。

        断开游戏后 `disable()` 同步生效 → enabled 立刻为假 → 回到【开始】，
        不用等任务线程收尾（用户 2026-11-02 口径保留）。
        """
        if task is None:
            return False
        return bool(getattr(task, "enabled", False))

    def _device_meta(self) -> str:
        try:
            device = og.device_manager.get_preferred_device()
        except Exception:  # noqa: BLE001
            device = None
        if not device:
            return "未连接 · 请在「截图方式」页选择窗口"
        exe = device.get("nick") or device.get("selected_exe") or device.get("device") or "windows"
        status = "已连接" if device.get("connected") else "未连接"
        return f"{exe} · {status}"

    # ---------- 操作 ----------

    def set_captain(self, name):
        """勾选框/下拉改队长：写回 task.config（Config 会自动存盘）。

        **换队长时「刷到目标分」的勾选和目标分保留**（用户 2026-11-08 口径）：
        「刷到目标分」是界面自己的开关（`task._score_stop_on`），它和
        agent 注入的队列参数是两回事 —— 换队长只重建队列参数为新队长
        +原目标分，不能把勾选/分数一起抹掉。

        **agent 注入的队列参数要清**（用户 2026-11-07 口径）：
        「每次指定新队长，旧的安排就清掉，不管单个指定还是传参进来的队列」
        —— 没勾「刷到目标分」时，换队长就把 `_queue_spec` 清空。

        运行期变量 `_style` 也清，流派回到「按队长重新认」。
        取消勾选（name 为空）时同样清 `_score_stop_on`。

        流派本身存在 `task._style`（运行期变量），config 里只存队长名字。
        """
        task = self._task()
        if task is None:
            logger.warning("监控页：没有找到训练任务，队长改动未保存")
            return
        value = (name or "").strip()
        if (task.config.get("指定队长", "") or "") != value:
            task.config["指定队长"] = value
            if value:
                logger.info(f"监控页：指定队长 → {value}")
            else:
                logger.info("监控页：取消指定队长 → 清空变量，流派改回按游戏内队长识别")
        # 队列参数：勾着「刷到目标分」→ 换成新队长（保留目标分）；
        # 没勾 → 清掉（可能是 agent 注入的多人队列，界面改队长即作废）。
        if getattr(task, "_score_stop_on", False) and value:
            old_target = self._spec_target(getattr(task, "_queue_spec", "") or "")
            task._queue_spec = f"{value}:{old_target}" if old_target else value
            logger.info(f"监控页：换队长 → 队列参数重建为「{task._queue_spec}」"
                        f"（目标分勾选保留）")
        elif getattr(task, "_queue_spec", None):
            task._queue_spec = None
            logger.info("监控页：界面指定队长 → 注入的多角色队列参数已清空")
        if not value:
            # 取消勾选队长 = 无队长模式，顺带关掉「刷到目标分」
            if getattr(task, "_score_stop_on", False):
                task._score_stop_on = False
                logger.info("监控页：取消指定队长 → 「刷到目标分」一并关闭")
                # 勾选框会自己变灰/取消，不解释一句用户会以为「界面把我的设置吃了」
                self._notify("已取消指定队长：没有队长就没法刷分，「刷到目标分」一并关闭")
            reset = getattr(task, "reset_style", None)
            if callable(reset):
                try:
                    reset()
                except Exception as e:  # noqa: BLE001
                    logger.error(f"重置流派失败: {e}")
        self._update_data()

    @staticmethod
    def _spec_target(spec: str) -> int:
        """从队列参数串里取目标分（`"队长:8000"` → 8000；没有返回 0）。"""
        try:
            from src.tasks.training.queue import parse_queue
            items = parse_queue(spec or "")
        except Exception:  # noqa: BLE001
            return 0
        for item in items:
            if item.get("target"):
                return int(item["target"])
        return 0

    @staticmethod
    def _captains(captain: str = "") -> dict:
        """流派 → [角色名]，数据来自业务常量 STYLE_BY_NAME。

        显示哪些分组由 `CAPTAIN_DROPDOWN_STYLES` 决定（用户 2026-11-08：
        先只保留训练流 / 扭蛋流，商店流 / 小游戏流先隐藏）。

        `captain` = 配置里当前的队长名：它那一组**不管隐藏与否都要在**——
        下拉里没有这个人，`_sync_captain` 就回填不到，勾选框一勾
        就会退回「第一个训练流角色」，把正在跑的队长悄悄换掉。
        """
        try:
            from src.tasks.training.constants import (
                CAPTAIN_DROPDOWN_STYLES, STYLE_BY_NAME)
        except Exception as e:  # noqa: BLE001
            logger.error(f"读取角色表失败: {e}")
            return {}
        groups: dict[str, list[str]] = {}
        for name, style in STYLE_BY_NAME.items():
            groups.setdefault(style, []).append(name)
        order = ["训练流", "商店流", "扭蛋流", "小游戏流"]
        keys = [key for key in order if key in CAPTAIN_DROPDOWN_STYLES]
        if not keys:                      # 常量写空了也别让下拉整个空掉
            keys = list(order)
        if captain:
            try:
                from src.tasks.training.parsers import TrainingParsersMixin
                style = TrainingParsersMixin.style_for_name(captain)
            except Exception:             # noqa: BLE001
                style = ""
            if style and style in groups and style not in keys:
                keys = [key for key in order if key in set(keys) | {style}]
        return {key: sorted(groups[key]) for key in keys if key in groups}

    # ---------- 快捷操作 ----------

    def toggle_capture(self):
        """等同「截图方式」页的截图按钮。"""
        try:
            og.main_window.start_tab.capture()
        except Exception as e:  # noqa: BLE001
            logger.error(f"开始截图失败: {e}")
        self._update_data()

    def toggle_connection(self):
        """第一栏总开关：【启动 / 停止】（用户 2026-11-01/02 口径）。

        现在的口径：
        - **启动**：接入游戏（连设备、开跑）。交给 `start_controller.start()`，
          它会自己刷新设备、必要时启动游戏 exe。
        - **停止**：断开游戏 —— 停掉自动训练任务、暂停执行器、释放截图会话，
          **并把第二栏复位成【开始】**（未启动状态）。

        判据是「游戏接上了没有」（`_connected()`），不是执行器的 paused：
        后者是执行器的运行态，会被第二栏的动作带动（见 `_connected()` 注释）。
        """
        if self._connected():
            self._connection_stop()
        else:
            self._connection_start()

    def _bring_game_to_front(self):
        """把**已经在跑**的游戏窗口恢复并置前（最小化/被别的窗口挡住时）。

        为什么需要（2026-11-08 用户实测）：游戏进程一直在跑，但窗口在后台
        或最小化了。框架 `start_controller.start_device()` 只在
        `device['connected']` 为假时才执行 exe / 等窗口；已连接时只
        `do_refresh` —— 窗口不会被恢复，用户看到的就是"点了启动却什么都没
        发生"（而且 WGC 对最小化窗口零帧，脚本也跑不动）。

        走 `GirlsInteraction.ensure_foreground()`（项目唯一的置前实现：
        IsIconic → SW_RESTORE、Alt 注入、AttachThreadInput、TopMost 三重保险，
        并且**先等用户空闲**才抢，不打断正在操作的人）。进程没起时它返回
        False，拉起 exe 仍归 `start_controller`。
        """
        try:
            interaction = getattr(self.executor, "interaction", None)
            ensure = getattr(interaction, "ensure_foreground", None)
            if callable(ensure):
                ok = ensure()
                logger.info(f"监控页：游戏窗口置前 → {'成功' if ok else '未成功（窗口可能不存在）'}")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"置前游戏窗口失败（不影响启动）: {e}")

    def _connection_start(self):
        """启动 = 连接游戏（用户口径，从始至终未变）。

        分工：
        - 第一栏【启动/停止】只负责连接/断开游戏；
        - 第二栏【开始/暂停】只负责自动训练脚本；
        - 点第二栏【开始】时如果还没连接，才"顺手点一下【启动】"。

        **唯一的例外：agent 注入的队列参数**（`task._queue_spec`，多角色队列
        是 agent 的隐藏通道，没有实装到界面）。带参数进来时自动开跑
        （`start_controller.start(task)` = 连接 + enable，等于自动点【开始】）；
        参数为空则只连接（`start_controller.start()`，不带 task）。

        先把已在跑的游戏窗口恢复/置前（`_bring_game_to_front`），否则
        "点了启动没反应" —— 框架对已连接的设备不做任何窗口操作。
        """
        task = self._task()
        spec = (getattr(task, "_queue_spec", None) or "") if task else ""
        self._bring_game_to_front()
        try:
            if spec:
                logger.info(f"监控页：注入队列参数 → 启动即自动开跑（{spec}）")
                og.app.start_controller.start(task)
            else:
                og.app.start_controller.start()
        except Exception as e:  # noqa: BLE001
            logger.error(f"启动失败: {e}")
            self._notify(f"启动失败：{e}", error=True)
        self._update_data()

    def _connection_stop(self):
        """停止 = 全部停干净（对齐框架 TaskCard.stop_clicked：disable + unpause），
        另加用户 2026-11-07 口径的扩展：

        ① 训练脚本停（disable + unpause，**清 task.pause 的挂起态**）；
        ② 队列参数与进度一并清空；
        ③ 执行器暂停（不再取帧）；④ 截图会话关闭（断开游戏）；
        ⑤ 快速比赛开关复位成「关」。

        关截图会话可能等正在取的那一帧（最多 4 秒），放到后台线程做。
        """
        task = self._task()
        self._clear_queue_state()              # ② 队列参数/进度清空（先于 disable）
        self._stop_auto_training(reset_button=True)
        self._stop_quick_match()
        try:
            if self.executor is not None and not getattr(self.executor, "paused", True):
                self.executor.pause()
        except Exception as e:  # noqa: BLE001
            logger.error(f"暂停执行器失败: {e}")
        self._close_capture_async()
        logger.info("监控页：已停止（断开游戏：队列清空、第二栏复位成「开始」、快速比赛复位成「关」）")
        self._update_data()

    def _clear_queue_state(self):
        """清掉训练任务上的队列参数 / 内存队列 / 进度文件 / 目标分开关
        （第一栏【停止】与第二栏【■】共用的那半段逻辑）。任务不存在时静默跳过。

        「一切归零」：连界面「刷到目标分」的勾选也一起关掉（`_score_stop_on`），
        否则参数被清了、勾还亮着，界面在撒谎。
        """
        task = self._task()
        if task is None:
            return
        task._queue_spec = None
        task._queue = None
        task._score_stop_on = False
        try:
            from src.tasks.training.queue import QUEUE_PROGRESS
            if os.path.exists(QUEUE_PROGRESS):
                os.remove(QUEUE_PROGRESS)
        except OSError as e:
            logger.warning(f"删除队列进度文件失败: {e}")

    @staticmethod
    def _close_capture_async():
        """后台关掉截图会话（等价于「断开游戏」）。

        **先同步置 None，再后台 close**（2026-11-02 修正）：
        旧版把 `capture_method = None` 也放在后台线程里，于是断开后的
        几秒内 `_connected()` 仍返回 True —— 第一栏还显示【停止】、
        第二栏也以为还能开始。置 None 只是一次属性赋值（不阻塞），
        先做掉它，界面立刻进入「已断开」状态；真正耗时的 `close()`
        （要等正在取的那一帧，最多 4 秒）留到后台。
        """
        import threading

        device_manager = getattr(og, "device_manager", None)
        capture = getattr(device_manager, "capture_method", None) if device_manager else None
        if capture is None:
            return
        # 先同步断开连接态（属性赋值，不阻塞 UI）
        try:
            device_manager.capture_method = None
        except Exception as e:  # noqa: BLE001
            logger.error(f"断开截图（置空）失败: {e}")
            return

        def worker():
            try:
                if hasattr(capture, "close"):
                    capture.close()
            except Exception as e:  # noqa: BLE001
                logger.error(f"断开截图失败: {e}")

        threading.Thread(target=worker, name="MonitorDisconnect", daemon=True).start()

    def set_skill(self, key, value):
        """技能品质数量：写回任务配置并立刻重算预计分数。"""
        task = self._task()
        name = SKILL_KEYS.get(key)
        if task is None or name is None:
            return
        task.config[name] = max(0, int(value))
        self._update_data()

    # ---------- 目标分停 / 专注药（用户 2026-11-05；2026-11-07 改队列参数化） ----------
    #
    # 「刷到目标分」映射到**内存里的多角色队列参数**（`task._queue_spec`，
    # 不写任何配置键）：任务下一次 `run()` 的 `init_captain_queue()` 会拿它
    # 建队列。达标的判定和提前终止由 `handlers/match.handle_training_score`
    # （训练评分页）和 `flow.advance_queue_after_round`（注册球员页）做，
    # 任务自己会停，界面这里只负责挂参数。
    # - 「允许吃专注药」= `自动恢复精力`。钻石购买在 `handlers/stamina.py`
    #   是结构性禁止（钻石行没有「使用」按钮 → `forbidden`）。

    def set_score_stop(self, enabled: bool):
        """勾选「刷到目标分」：把当前队长 + 目标分挂成单人多角色队列参数。

        **先勾后填也允许**（2026-11-07 口径，修复「先有鸡还是先有蛋」死锁）：
        旧版勾上时要求输入框已有目标分，但输入框又只在勾选后才启用 ——
        两条规则互相锁死，用户永远勾不上（实测确认）。
        现在勾上只要求**已指定队长**；目标分可以之后填 —— 输入框一填数字
        （editingFinished）就自动更新队列参数（set_score_target）。

        - 勾上：队长必填（没有就拒绝 —— 队列空了会静默降级成单队长模式）；
          目标分暂时为空 → 队列参数挂「队长」（无目标），该角色按未达标
          一直刷，填入分数后下一局自动按目标判定。
        - 取消勾选：清掉队列参数，恢复「跑完一局就结束」的老行为。

        **勾选状态存在 `task._score_stop_on`**（界面自己的开关，和 agent 注入
        的队列参数分开）：换队长时勾选/目标分要保留，只重建队列参数。
        """
        task = self._task()
        if task is None:
            logger.warning("监控页：没有找到训练任务，「刷到目标分」未保存")
            return
        if not enabled:
            task._score_stop_on = False
            if getattr(task, "_queue_spec", None):
                task._queue_spec = None
                logger.info("监控页：刷到目标分 → 关（清队列参数，跑完一局就结束）")
            self._update_data()
            return
        captain = (task.config.get("指定队长") or "").strip()
        target = self._read_target(task)
        if not captain:
            logger.warning("监控页：勾「刷到目标分」失败 —— 请先在左侧指定队长")
            self._notify("勾选「刷到目标分」失败：请先在左侧指定队长", error=True)
            self.notify_stop_check()
            return
        task._score_stop_on = True
        task._queue_spec = f"{captain}:{target}" if target else captain
        if target:
            logger.info(f"监控页：刷到目标分 → 开（{captain} 刷到 {target} 分后停止）")
        else:
            logger.warning(f"监控页：刷到目标分 → 开（{captain}，目标分未填 —— "
                           f"填入「目标分」输入框后自动生效）")
            self._notify(f"「刷到目标分」已开（{captain}）：请在「目标分」里填分数，"
                         f"否则会一直刷到手动停止")
        self._update_data()

    def set_score_target(self, target: int):
        """目标分输入框：填数字就更新队列参数（先勾后填 / 先填后勾都支持）。"""
        task = self._task()
        if task is None:
            return
        target = max(0, int(target or 0))
        if not target:
            return                          # 清空/乱输入不动参数，等勾选时再校验
        if not getattr(task, "_score_stop_on", False):
            return                          # 没勾「刷到目标分」，只是提前记着，不生效
        captain = (task.config.get("指定队长") or "").strip()
        if not captain:
            return
        task._queue_spec = f"{captain}:{target}"
        logger.info(f"监控页：目标分 → {target}（{captain} 刷到 {target} 分后停止）")
        self._update_data()

    def _read_target(self, task) -> int:
        """读输入框里用户刚填的目标分（勾选时还没触发 editingFinished）。"""
        view = getattr(self, "_view", None)
        edit = getattr(view, "_edit_target", None)
        if edit is None:
            return 0
        text = (edit.text() or "").strip()
        return int(text) if text.isdigit() else 0

    @staticmethod
    def _notify(message: str, error: bool = False):
        """在界面上弹一条提示（InfoBar + 托盘），失败绝不影响主流程。

        **别用 `logger.warning(..., notify=True)`**：`ok.Logger.info/warning` 只收
        一个 message，多传 `notify` 就是 `TypeError` —— 而它发生在 Qt 槽里，
        异常被事件循环吞掉，表现成「提示从来没出现过」（2026-11-08 实测，
        本文件里 6 处这种调用全都没生效过）。

        合规通道是框架自己的 `ok.core.notifications`：
        `alert_info / alert_error` → `communicate.notification.emit(...)` →
        `MainWindow.show_notification`（qfw InfoBar，必要时加托盘/外部通知）。
        走 signal 发送，跨线程安全（任务线程里调用也不会摸 Qt 控件）。
        """
        try:
            from ok.core.notifications import alert_error, alert_info
            (alert_error if error else alert_info)(message)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"界面提示发送失败（只写日志）: {e}")

    def notify_stop_check(self):
        """校验失败把勾选框弹回去（view 层按 config 回填，这里强制刷一次）。"""
        view = getattr(self, "_view", None)
        if view is not None:
            task = self._task()
            view._syncing_extras = False
            view._sync_extras({"score_stop": bool(getattr(task, "_score_stop_on", False))
                               if task else False})

    def set_stamina_allowed(self, allowed: bool):
        """「允许吃专注药」：映射到 `自动恢复精力`。

        关掉后 `ensure_stamina` 不再进恢复页；「训练所需专注还差 N」弹窗
        仍会点「回复」进页 —— 但没有可用饮料时（用户不勾 = 通常没饮料）
        `handle_stamina_recover` 会「没有可喝的 → 关页」，不会喝、更不会
        碰钻石行（钻石行永远 `forbidden`，写死在 stamina.py）。
        """
        task = self._task()
        if task is None:
            logger.warning("监控页：没有找到训练任务，「允许吃专注药」未保存")
            return
        value = bool(allowed)
        if bool(task.config.get("自动恢复精力")) != value:
            task.config["自动恢复精力"] = value
            logger.info("监控页：允许吃专注药 → " + ("开（专注不足自动喝饮料，不花钻石）"
                                              if value else "关（专注不足也不喝，宁停不耗）"))
        self._update_data()

    def set_quick_match(self, enabled):
        """队长栏右侧的「快速比赛」开关 —— **只管快速比赛这个模块**。

        规则（用户 2026-11-02 口径，两个模块对称）：

        1. **执行器**（`og.executor`）是唯一的运行基础，**只有第一栏
           【启动/停止】能动它**：启动 = 接入 + `executor.start()`，
           停止 = 两个开关复位 + `executor.pause()` + 释放截图会话。
        2. **开**：enable `QuickMatchTask`，执行器还停着就**顺手**
           `executor.start()`（`_wake_executor`，拉游戏 / 连设备仍归【启动】）。
        3. **关**：只 `task.disable()` —— **绝不去 pause 执行器**，
           关执行器不归它管（第二栏【暂停】也一样）。
        4. **执行器被关掉时**（第一栏【停止】）：本开关跟着复位成「关」
           （`_stop_quick_match`），运行基础没了就别装作还在跑。

        分工上两个模块无关：自动训练自己处理养成里的比赛；快速比赛管
        **养成之外**的比赛（全国大赛 / 圣山战 / 竞技场），养成跑着时它自动让位
        （`next_task()` 里 onetime 优先）。

        旧版只 `task.enable()`：`TriggerTask` 只在执行器没被暂停时才会被调度，
        于是开关拨到「开」什么都不动，非得有人先按第二栏【开始】——
        用户看到的就是「快速比赛跟第二栏的开始绑在一起」，其实是没人唤醒执行器。
        """
        task = self._task_by_name("QuickMatchTask")
        if task is None:
            logger.warning("监控页：没找到「快速比赛」任务")
            return
        try:
            if enabled:
                task.enable()
            else:
                task.disable()
        except Exception as e:  # noqa: BLE001
            logger.error(f"快速比赛 → {'开' if enabled else '关'}失败: {e}")
            self._update_data()
            return
        if enabled:
            # 开：执行器还停着就顺手唤醒（不是必须，接入仍归【启动】）
            self._wake_executor()
            logger.info("监控页：快速比赛 → 开")
        else:
            # 关：只关自己。执行器归第一栏【停止】管，这里不碰
            logger.info("监控页：快速比赛 → 关（不动执行器）")
        self._update_data()

    def _wake_executor(self):
        """顺手把执行器从「暂停」里放出来 —— **只唤醒，不接入**。

        `executor.start()` 只是把 `paused` 置 False 并唤醒调度循环；刷新设备、
        拉起游戏 exe 这些「接入」归第一栏【启动】（`start_controller`）。
        所以这里是「顺手而不是必须」：游戏没开着时唤醒也拿不到帧，
        用户还是得按【启动】。返回是否真的动过执行器。
        """
        try:
            if self.executor is not None and getattr(self.executor, "paused", True):
                self.executor.start()
                logger.info("监控页：顺手唤醒执行器（接入游戏仍是第一栏【启动】的事）")
                return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"唤醒执行器失败: {e}")
        return False

    def toggle_auto_training(self):
        """第二栏按钮：【开始 / 暂停】—— **只管自动训练脚本**（用户口径）。

        分工（从始至终未变）：
        - 第一栏【启动/停止】只负责连接/断开游戏；
        - 第二栏【开始/暂停】只负责自动训练脚本；
        - 点【开始】时**如果还没连接游戏**，顺手替你点一下第一栏【启动】。

        三态：
        - 在跑 → `task.pause()` 挂起，按钮回【开始】（可恢复）；
        - 已暂停 → `task.unpause()` 恢复；
        - 未启动：
          - 已连接 → `task.enable()` 开跑；
          - 未连接 → `start_controller.start(task)`（**这次带 task**：
            顺手连接 + 连接完成后 enable，等于"点了【启动】再点【开始】"）。
        """
        task = self._task()
        if task is None:
            logger.warning("监控页：没找到训练任务")
            self._notify("没找到训练任务：可能还在加载，稍后再点一次", error=True)
            return
        enabled = bool(getattr(task, "enabled", False))
        paused = enabled and bool(getattr(task, "paused", False))
        running = enabled and not paused and bool(getattr(task, "running", False))
        try:
            if running:
                # 在跑 → 暂停（框架 pause_clicked：task.pause()）
                try:
                    task.pause()
                except Exception as e:              # noqa: BLE001
                    # 框架的 pause(task) 在 current_task != 自己时会抛（刚好在换任务）。
                    # 暂停意图仍然要落地，否则按钮点了没反应、脚本继续抢鼠标。
                    logger.warning(f"框架 pause 失败，直接置暂停标志: {e}")
                    task._paused = True
                logger.info("监控页：自动训练 → 暂停（task.pause，可无损恢复）")
            elif paused:
                # 已暂停 → 恢复（框架 start_clicked 第一分支）
                task.unpause()
                logger.info("监控页：自动训练 → 继续（task.unpause）")
            elif self._connected():
                # 已连接：只开跑自动训练（不碰连接，那是第一栏的事）
                task.enable()
                logger.info("监控页：自动训练 → 开始（已连接，仅 enable 任务）")
            else:
                # 未连接：顺手点一下【启动】—— 连接 + 连接完成后 enable。
                # 接入是异步的，期间重复点击不重复发起（否则队列里堆 do_start）。
                if self._starting():
                    logger.info("监控页：正在接入游戏，稍候…（不重复发起）")
                else:
                    logger.info("监控页：还没连接游戏 → 顺手启动（连接并开跑）")
                    self._bring_game_to_front()
                    og.app.start_controller.start(task)
        except Exception as e:  # noqa: BLE001
            logger.error(f"自动训练开关失败: {e}")
        self._update_data()

    @staticmethod
    def _starting() -> bool:
        """`start_controller.start()` 已发起、还没跑完（框架的 `starting` 标志）。

        界面靠它把第二栏显示成【启动中…】：接入要等窗口稳定 + 设备就绪，
        这期间按钮既不是「开始」也不是「暂停」，不显示点什么就又像「没反应」。
        """
        controller = getattr(getattr(og, "app", None), "start_controller", None)
        return bool(getattr(controller, "starting", False))

    def _stop_quick_match(self):
        """执行器被关掉时把「快速比赛」也关掉，开关复位成「关」。

        运行基础（执行器 + 截图会话）没了，挂在上面的模块开关就不能还是「开」：
        否则界面显示「开」而实际一帧都拿不到 —— 那是断开链路没收尾。
        同类复位还有第二栏（`_stop_auto_training(reset_button=True)`）。

        **只在断开时做**：快速比赛拨「关」只 disable 它自己，
        绝不会反过来 pause 执行器（关执行器不是它该管的事）。
        """
        task = self._task_by_name("QuickMatchTask")
        if task is None or not getattr(task, "enabled", False):
            return
        try:
            task.disable()
            logger.info("监控页：断开游戏 → 快速比赛已关闭（开关复位成「关」）")
        except Exception as e:  # noqa: BLE001
            logger.error(f"关闭快速比赛失败: {e}")

    def _stop_auto_training(self, reset_button=False):
        """停掉自动训练任务（对齐框架 stop_clicked：disable + unpause）。

        框架 TaskCard.stop_clicked = `task.disable(); task.unpause()` ——
        unpause 清掉 task.pause() 的挂起态（我们第二栏现在用框架的
        task.pause() 暂停，停止时必须解除，否则下次【开始】会走 resume）。
        这在第一栏【停止】（要全部停干净）是正确语义。

        `reset_button=True` 时把第二栏按钮复位成「开始」（未启动状态）——
        第一栏点【停止】后要连带复位，用户再点一次就能直接重新开始。
        """
        task = self._task()
        if task is not None:
            try:
                task.disable()
                task.unpause()          # 框架 stop_clicked 同款：清 pause 挂起态
            except Exception as e:  # noqa: BLE001
                logger.error(f"停止自动训练失败: {e}")
        if reset_button:
            logger.info("监控页：自动训练 → 已停止（按钮复位为「开始」）")

    def hard_stop_training(self):
        """第二栏的【■ 完全停止】（用户 2026-11-07 口径）。

        与【暂停】的差别：暂停只是 `task.disable()`，注入的多角色队列参数
        还挂在任务上，下一次【开始】会接着队列跑；完全停止要**把队列参数
        和队列进度一起清掉**（`_clear_queue_state`），回到「按界面配置跑
        一局就结束」的干净状态。

        **不断开游戏连接**：执行器、截图会话、快速比赛都不碰 —— 那是第一栏
        【停止】的职责（它 additionally 关执行器 + 断连接 + 关快速比赛）。
        """
        task = self._task()
        if task is None:
            logger.warning("监控页：没有找到训练任务，完全停止未执行")
            return
        try:
            task.disable()
        except Exception as e:  # noqa: BLE001
            logger.error(f"停止自动训练失败: {e}")
        had_queue = bool(getattr(task, "_queue_spec", None) or
                         getattr(task, "_queue", None))
        self._clear_queue_state()
        if had_queue:
            logger.info("监控页：完全停止 → 队列参数与进度已清空")
        else:
            logger.info("监控页：完全停止 → 脚本已停（没有队列参数）")
        # 接入中点停止的边界：start_controller 的接入流程走完后会把任务
        # 重新 enable（_mark_task_enabled），必须再补一刀 disable —— 用
        # 短延时轮询到「接入结束」为止（框架没有取消接入的接口）。
        if self._starting():
            logger.info("监控页：接入仍在进行 → 接入完成后自动补一刀停止")

            def _late_disable(tries=0):
                controller = getattr(getattr(og, "app", None), "start_controller", None)
                still = bool(getattr(controller, "starting", False)) if controller else False
                if still and tries < 120:            # 最多等 60 秒（500ms × 120）
                    QTimer.singleShot(500, lambda: _late_disable(tries + 1))
                    return
                t = self._task()
                if t is not None and getattr(t, "enabled", False):
                    try:
                        t.disable()
                        self._clear_queue_state()
                        logger.info("监控页：接入完成后已补停（队列参数同步清空）")
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"接入完成后补停失败: {e}")

            QTimer.singleShot(500, _late_disable)
        self._update_data()

    def _task_by_name(self, name):
        try:
            for task in getattr(self.executor, "trigger_tasks", []) or []:
                if task.__class__.__name__ == name:
                    return task
            for task in getattr(self.executor, "onetime_tasks", []) or []:
                if task.__class__.__name__ == name:
                    return task
        except Exception:  # noqa: BLE001
            pass
        return None

    @staticmethod
    def _enabled_of(task) -> bool:
        return bool(getattr(task, "enabled", False)) if task is not None else False

    @staticmethod
    def _running_of(task) -> bool:
        """任务是不是在跑（正在执行）或在队列里等。

        第二栏的【开始/暂停】按这个切文案：
        在跑 → 「暂停」；没跑 → 「开始」。
        """
        if task is None:
            return False
        return bool(getattr(task, "running", False) or getattr(task, "enabled", False))

    def refresh(self):
        """重新扫一遍窗口，再刷一次页面数据。"""
        try:
            refresh = getattr(og.device_manager, "refresh", None)
            if callable(refresh):
                refresh()
        except Exception as e:  # noqa: BLE001
            logger.error(f"刷新设备失败: {e}")
        self._update_data()

    def open_logs(self):
        try:
            from ok.util.explorer import open_explorer_folder
            open_explorer_folder(get_relative_path("logs"))
        except Exception as e:  # noqa: BLE001
            logger.error(f"打开日志目录失败: {e}")


def _locate(root, path):
    """按 path 取节点：("sections", 2, "items", 1, "rows", 0, 1)。"""
    node = root
    for step in path:
        node = node[step]
    return node


def _fmt_uptime(task) -> str:
    start = getattr(task, "start_time", 0) or 0
    if not start:
        return MISSING
    seconds = max(0, int(time.time() - start))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
