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
        """任务是不是**真的在跑**（不是「只是排上队了」，也不是「正在收尾」）。

        判据是 `enabled and running`，两个都要：
        - 只有 `running` 不够：断开游戏后 `disable()` 是**同步**生效的（`_enabled`
          立刻变 False），但 `running` 要等任务线程跑完当前拍才置 False ——
          中间有几秒窗口，界面会一直显示【暂停】，而任务其实已经被停掉了。
          用户 2026-11-02 口径：断开后第二栏要立刻回到【开始】。
        - 只有 `enabled` 不够：`enabled` 含「在队列里等」，
          点完【开始】按钮立刻变【暂停】，但其实一帧都还没跑。

        所以：**enabled（没被停）且 running（真在跑）** 才算在跑。
        """
        if task is None:
            return False
        return bool(getattr(task, "enabled", False)
                    and getattr(task, "running", False))

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

    def _connection_start(self):
        """启动 = 接入游戏 + 开跑自动训练 —— **执行器归它拉起来**。

        交给 `start_controller.start(task)`：它会刷新设备、必要时拉起游戏 exe、
        把训练任务 enable 并入队、启动执行器 —— 一条龙。

        为什么带 task：只调 `start()`（不带 task）只会连上设备，
        训练任务没被 enable，界面上「已连接」但**什么都不跑**。

        分工（用户 2026-11-02 口径）：执行器由这里拉起，**不是**由第二栏【开始】
        或快速比赛开关拉起 —— 那两处只是「顺手」唤醒（`_wake_executor`），
        拉游戏 / 连设备这种接入动作只在这里做。

        注意：这里 enable 训练任务**不算**「第二栏被第一栏带动」——
        第一栏的语义是「接入游戏并开跑」，本来就包含让脚本跑起来。
        第二栏仍可独立【暂停】（只停训练脚本，不影响连接）。
        """
        task = self._task()
        try:
            og.app.start_controller.start(task)
        except Exception as e:  # noqa: BLE001
            logger.error(f"启动失败: {e}")
        self._update_data()

    def _connection_stop(self):
        """断开游戏 = 关掉执行器这个「运行基础」：两栏的开关一起复位。

        顺序有讲究：先把任务 disable（任务线程下一拍就退出），
        再暂停执行器（不再取帧），最后才关截图会话。

        关截图会话可能等正在取的那一帧（最多 4 秒），所以放到后台线程做，
        不然界面会卡住不动。

        **执行器是唯一的运行基础，只有这里能动它**（用户 2026-11-02 口径）：
        运行基础关掉了，挂在它上面的两个模块开关都得跟着关 ——
        - 第二栏复位成【开始】（`_stop_auto_training(reset_button=True)`）；
        - 「快速比赛」开关复位成「关」（`_stop_quick_match`）。

        反过来不成立：第二栏【暂停】和快速比赛拨「关」**只 disable 自己的任务**，
        绝不去 pause 执行器 —— 关执行器不是它们该管的事。
        """
        self._stop_auto_training(reset_button=True)
        self._stop_quick_match()
        try:
            if self.executor is not None and not getattr(self.executor, "paused", True):
                self.executor.pause()
        except Exception as e:  # noqa: BLE001
            logger.error(f"暂停执行器失败: {e}")
        self._close_capture_async()
        logger.info("监控页：已停止（断开游戏：第二栏复位成「开始」、快速比赛复位成「关」）")
        self._update_data()

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
        """第二栏按钮：【开始 / 暂停】—— 只管自动训练脚本自己。

        跟快速比赛开关**对称**的规则（用户 2026-11-02 口径）：

        1. **执行器**是唯一的运行基础，只有第一栏【启动/停止】能动它；
        2. **开始**：enable 训练任务；执行器还停着就**顺手**
           `executor.start()`（`_wake_executor`）。没接上游戏时先走
           `_auto_start()` 接入（接入链路本身会 enable 任务 + 拉起执行器）；
        3. **暂停**：只 `task.disable()` —— **绝不去 pause 执行器**
           （关执行器不归第二栏管，快速比赛拨「关」也一样）；
        4. **执行器被关掉时**（第一栏【停止】）：本栏复位成【开始】
           （`_stop_auto_training(reset_button=True)`），快速比赛开关同时复位。

        **不碰「快速比赛」**：它有自己那一行开关，由它自己管自己。

        **不调 `task.unpause()`**（2026-11-02 修正）：
        框架的 `unpause()` 实体是 `self._paused = False; self.executor.start()`，
        它会**把整个执行器从暂停里放出来** —— 于是第二栏一点【暂停】，
        第一栏（判据是执行器状态时）就从「启动」跳回「停止」，
        表现成「第二栏带动第一栏」。
        第二栏从来没调过 `task.pause()`，所以压根不需要 unpause。
        """
        task = self._task()
        if task is None:
            logger.warning("监控页：没找到训练任务")
            return
        if getattr(task, "running", False) or getattr(task, "enabled", False):
            self._stop_auto_training(reset_button=True)
        elif not self._connected():
            # 没接上游戏：不 enable（任务只会躺在队列里，一帧都不跑，
            # 界面也不会有任何变化），改成主动接入 —— 见 `_auto_start`。
            self._auto_start()
        else:
            try:
                task.enable()
                # 任务只是入队；执行器要是还停着就没人取它 —— **顺手**唤醒一下
                # （执行器真正归第一栏【启动】管，这里不是必须）。
                # 唤醒执行器**不影响第一栏**（第一栏看的是连接态）。
                self._wake_executor()
                logger.info("监控页：自动训练 → 开始")
            except Exception as e:  # noqa: BLE001
                logger.error(f"开始自动训练失败: {e}")
        self._update_data()

    def _auto_start(self):
        """第二栏【开始】的前置：没接上游戏时**主动接入**（用户 2026-11-02 口径）。

        走第一栏 `_connection_start()` 的同一条链路（`start_controller.start(task)`：
        刷新设备 → 必要时拉起游戏 exe → 等窗口稳定 → enable 任务 → 执行器启动），
        所以这里**不能**再自己 `task.enable()`：
        1. `start_controller` 自己会 enable 这个 task（`_mark_task_enabled`）；
        2. 还没接上就 enable，任务只会排在队列里等，按钮文案也不会变
           —— 就是上一版「点了没反应」的样子。

        接入是异步的（`start()` 只是 post 到 handler 线程，几秒到几十秒），
        期间再点一次不重复发起，否则队列里会堆好几个 `do_start`、重复拉游戏窗口。
        """
        if self._starting():
            logger.info("监控页：正在接入游戏，稍候…（不重复发起）")
            return
        logger.info("监控页：还没接上游戏 → 主动接入（等同第一栏【启动】）")
        self._connection_start()

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
        """停掉自动训练任务（**只停这个任务，不碰执行器和连接**）。

        2026-11-02 修正：旧版这里还调了 `task.unpause()`，
        它的实体是 `executor.start()` —— 会把执行器从暂停里放出来，
        于是「停自动训练」顺手把「断开游戏」也撤销了：
        第一栏刚点【停止】（执行器已 pause、截图会话已释放），
        再点第二栏【暂停】就把执行器唤醒了 → 第一栏自己跳回「停止」，
        而且执行器去取帧时 `capture_method` 已经是 None → 拿不到帧。
        `unpause()` 是用来解除 `task.pause()` 的，第二栏从没 pause 过，多余。

        `reset_button=True` 时把第二栏按钮复位成「开始」（未启动状态）——
        第一栏点【停止】后要连带复位，用户再点一次就能直接重新开始。
        """
        task = self._task()
        if task is not None:
            try:
                task.disable()
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
