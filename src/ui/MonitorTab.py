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

DEFAULT_LAYOUT = {
    "version": 1,
    "sections": [
        {"type": "connection", "height": 54,
         "title": "{device_title}", "meta": "{device_meta}",
         "show_run": True, "run_text": "启动"},
        {"type": "captain", "height": 54,
         "label": "指定队长",
         "hint": "取消勾选 = 不接开局，只跑已有局",
         "show_quick_match": True, "show_auto_training": True,
         "start_text": "开始", "pause_text": "暂停", "stop_text": "停止"},
        {"type": "hero", "height": 204,
         "kicker": "当前进度",
         "title": "第 {week} 周 {day}",
         "fallback": "等待开始",
         "now_label": "正在执行",
         "now": "{action}",
         "hint": "{task_hint}",
         "start_text": "▶ 开始",
         "pause_text": "暂停",
         "stop_text": "停止",
         "right_ratio": 0.5,
         "stats": [
             {"label": "体力", "value": "{stamina}", "suffix": "/{stamina_max}",
              "bar": True, "bar_key": "stamina_pct", "bar_width": 150},
             {"label": "零用钱", "value": "{coin}"},
             {"label": "流派", "value": "{style}"},
             {"label": "已运行", "value": "{uptime}"},
         ]},
        {"type": "cards", "height": 126, "items": [
            {"id": "today", "title": "今日计划", "rows": [
                ["比赛倒计时", "{match_countdown}"],
                ["周末去处", "{weekend_plan}"],
                ["野餐阈值", "体力 < {picnic_threshold}"],
            ]},
            {"id": "shop", "title": "商店", "rows": [
                ["购买策略", "{buy_strategy}"],
                ["赛前清空", "{clear_balance}"],
                ["今日已逛", "{shop_today}"],
            ]},
            {"id": "risk", "title": "风险", "rows": [
                ["当前失败率", "{fail_rate}"],
                ["失败率上限", "{fail_limit}"],
                ["卡死判定", "{stuck}"],
            ]},
        ]},
        {"type": "timeline", "title": "最近动作", "max_rows": 8,
         "time_width": 62, "event_width": 80},
    ],
}


class _Actions(MonitorActions):
    def __init__(self, tab: "MonitorTab"):
        self.tab = tab

    def start(self):
        self.tab.start_task()

    def pause(self):
        self.tab.pause_task()

    def stop(self):
        self.tab.stop_task()

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

    def toggle_auto_training(self):
        self.tab.toggle_auto_training()


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
        self._view = MonitorView(self._layout_data, _Actions(self), editing=self._editing,
                                  captains=self._captains())
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
        total_attr = sum(v for v in attrs.values() if isinstance(v, (int, float)))
        score = None
        if total_attr:
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
            "buy_strategy": config.get("购买策略", MISSING),
            "clear_balance": "打开" if config.get("赛前清空余额") else "关闭",
            "shop_today": info.get("shop_today") or "否",
            "fail_rate": info.get("fail_rate") or info.get("当前失败率") or MISSING,
            "captain": (config.get("指定队长") or "").strip(),
            # 第一栏总开关的状态：执行器没被按住 = 已接入游戏
            "executor_running": not getattr(self.executor, "paused", True) if self.executor else False,
            # 第二栏按钮的状态：自动训练任务在跑/在队列里
            "training_running": self._running_of(task),
            "attrs": attrs,
            "score": score,
            "skill_color": skills["color"],
            "skill_gold": skills["gold"],
            "skill_white": skills["white"],
            "skill_gained": (gained.get("color", 0) + gained.get("gold", 0)
                             + gained.get("white", 0)),
            "skill_last": info.get("skill_last") or "",
            "quick_enabled": self._enabled_of(self._task_by_name("QuickMatchTask")),
            "fail_limit": config.get("失败率上限(%)", MISSING),
            "stuck": info.get("stuck") or f"{config.get('卡死判定(秒)', MISSING)}s · 未触发",
            "timeline": info.get("timeline") or [],
            "device_title": "篮球少女：飞跃巅峰",
            "device_meta": self._device_meta(),
        }
        return data

    @staticmethod
    def _style_from_config(config: dict) -> str:
        """配置里的指定队长 → 流派；查不到就按训练流（和 flow.style 的兜底一致）。"""
        name = (config.get("指定队长") or "").strip()
        if name:
            try:
                from src.tasks.training.constants import STYLE_BY_NAME
                from src.tasks.ui.labels import name_matches
                for full, style in STYLE_BY_NAME.items():
                    if name_matches(name, full):
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

    def start_task(self):
        task = self._task()
        if task is None:
            return
        try:
            og.app.start_controller.start(task)
        except Exception as e:  # noqa: BLE001
            logger.error(f"启动任务失败: {e}")

    def pause_task(self):
        task = self._task()
        if task is not None:
            task.pause()

    def stop_task(self):
        task = self._task()
        if task is not None:
            task.disable()
            task.unpause()

    def set_captain(self, name):
        """勾选框/下拉改队长：写回 task.config（Config 会自动存盘）。

        **取消勾选 = 清空变量**（用户 2026-11-01 口径）：
        旧版只在「勾选框被取消」时才清空，但页面初始化时勾选框是**默认勾上**的，
        于是第一次进页面就等于「指定了一个队长」，流派被写成这个队长的
        （扭蛋流扭到训练流），用户后面再取消勾选也晚了 ——
        因为取消勾选时变量里已经是名字，界面上的勾选框状态和配置对不上。

        现在这里做两件事：
        1. 取消勾选 → 名字置空（`指定队长` = ""），并且**清掉已定的流派**，
           让下一次跑重新按日历/游戏内队长认流派；
        2. 勾上 → 用当前下拉里的角色名。

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
        # 运行期变量（`_style`）也要跟着清：不然旧流派会一直留在 _style 里，
        # 界面上的「流派」和实际跑的还是它 —— 取消勾选后必须回到「按日历认队长」。
        # 注意**不能**因为 config 没变就跳过：用户可能就是想让已定的流派重置。
        if not value:
            reset = getattr(task, "reset_style", None)
            if callable(reset):
                try:
                    reset()
                except Exception as e:  # noqa: BLE001
                    logger.error(f"重置流派失败: {e}")
        self._update_data()

    @staticmethod
    def _captains() -> dict:
        """流派 → [角色名]，数据来自业务常量 STYLE_BY_NAME。"""
        try:
            from src.tasks.training.constants import STYLE_BY_NAME
        except Exception as e:  # noqa: BLE001
            logger.error(f"读取角色表失败: {e}")
            return {}
        groups: dict[str, list[str]] = {}
        for name, style in STYLE_BY_NAME.items():
            groups.setdefault(style, []).append(name)
        order = ["训练流", "商店流", "扭蛋流", "小游戏流"]
        return {key: sorted(groups[key]) for key in order if key in groups}

    # ---------- 快捷操作 ----------

    def toggle_capture(self):
        """等同「截图方式」页的截图按钮。"""
        try:
            og.main_window.start_tab.capture()
        except Exception as e:  # noqa: BLE001
            logger.error(f"开始截图失败: {e}")
        self._update_data()

    def toggle_connection(self):
        """第一栏总开关：【启动 / 停止】（用户 2026-11-01 口径）。

        旧版这里是「开始 / 暂停」，按的是 `executor.pause()` —— 逻辑整个反了：
        1. 「暂停」只是按住执行器，第二栏的自动训练跟着一起被按住，
           表现成「第二栏的开关带动第一栏的开关」；
        2. 停止时没有断开游戏，脚本还挂着游戏窗口不放。

        现在的口径：
        - **启动**：接入游戏（连设备、开跑）。交给 `start_controller.start()`，
          它会自己刷新设备、必要时启动游戏 exe。
        - **停止**：断开游戏 —— 停掉自动训练任务、暂停执行器、释放截图会话。
          同时把第二栏的【开始/暂停】复位成「开始」（原始状态）。
        """
        if getattr(self.executor, "paused", True):
            self._connection_start()
        else:
            self._connection_stop()

    def _connection_start(self):
        """启动 = 接入游戏 + 开跑。

        交给 `start_controller.start(task)`：它会刷新设备、必要时拉起游戏 exe、
        把训练任务 enable 并入队、启动执行器 —— 一条龙。

        为什么带 task：只调 `start()`（不带 task）只会连上设备，
        训练任务没被 enable，界面上「已连接」但**什么都不跑**。
        第二栏的按钮会随之显示「暂停」（那是脚本的真实状态），
        但**不是**第二栏驱动了第一栏 —— 是总开关启动后脚本本来就在跑。
        """
        task = self._task()
        try:
            og.app.start_controller.start(task)
        except Exception as e:  # noqa: BLE001
            logger.error(f"启动失败: {e}")
        self._update_data()

    def _connection_stop(self):
        """断开游戏：任务停掉 + 执行器暂停 + 截图会话释放。

        顺序有讲究：先把任务 disable（任务线程下一拍就退出），
        再暂停执行器（不再取帧），最后才关截图会话。

        关截图会话可能等正在取的那一帧（最多 4 秒），所以放到后台线程做，
        不然界面会卡住不动。
        """
        self._stop_auto_training(reset_button=True)
        try:
            if self.executor is not None and not getattr(self.executor, "paused", True):
                self.executor.pause()
        except Exception as e:  # noqa: BLE001
            logger.error(f"暂停执行器失败: {e}")
        self._close_capture_async()
        logger.info("监控页：已停止（断开游戏）")
        self._update_data()

    @staticmethod
    def _close_capture_async():
        """后台关掉截图会话（等价于「断开游戏」）。"""
        import threading

        def worker():
            try:
                device_manager = getattr(og, "device_manager", None)
                capture = getattr(device_manager, "capture_method", None)
                if capture is not None and hasattr(capture, "close"):
                    capture.close()
                    device_manager.capture_method = None
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

    def set_quick_match(self, enabled):
        task = self._task_by_name("QuickMatchTask")
        if task is None:
            logger.warning("监控页：没找到「快速比赛」任务")
            return
        if enabled:
            task.enable()
        else:
            task.disable()
        logger.info(f"监控页：快速比赛 → {'开' if enabled else '关'}")

    def toggle_auto_training(self):
        """第二栏按钮：【开始 / 暂停】—— 只管自动训练脚本自己。

        不碰「接入游戏」：那是第一栏总开关的事。
        - 开始：把训练任务 enable 起来（前提是已经接上游戏，否则点了也没用）；
        - 暂停：把训练任务 disable（跑完当前动作就退出，状态回到「未开始」）。
        """
        task = self._task()
        if task is None:
            logger.warning("监控页：没找到训练任务")
            return
        if getattr(task, "enabled", False) or getattr(task, "running", False):
            self._stop_auto_training(reset_button=True)
        else:
            try:
                task.enable()
                logger.info("监控页：自动训练 → 开始")
            except Exception as e:  # noqa: BLE001
                logger.error(f"开始自动训练失败: {e}")
        self._update_data()

    def _stop_auto_training(self, reset_button=False):
        """停掉自动训练任务。

        `reset_button=True` 时把第二栏按钮复位成「开始」（原始状态）——
        第一栏点「停止」后要连带复位，用户再点一次就能直接重新开始。
        """
        task = self._task()
        if task is not None:
            try:
                task.disable()
                task.unpause()
            except Exception as e:  # noqa: BLE001
                logger.error(f"停止自动训练失败: {e}")
        if reset_button:
            logger.info("监控页：自动训练 → 已停止（按钮复位为「开始」）")

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
