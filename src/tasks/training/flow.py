"""单拍状态机、存证、卡死检测。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
"""

import json
import os
import random
import re
import threading
import time
from collections import deque

import cv2
import numpy as np

from src.tasks.training.constants import (JPEG_PARAMS, PROJECT_ROOT,
                                          THURSDAY_REST_THRESHOLD,
                                          WEEKEND_PLAN)  # noqa: F401
from src.util.emergency_stop import STOP

# 点击流水：一行一个 JSON 事件（时间戳 / 步数 / 当时界面 / 坐标）。
# 放在 logs/ 下，已被 .gitignore 忽略；任务结束后用它复盘「第几秒点了哪里」。
CLICK_LOG = os.path.join(PROJECT_ROOT, "logs", "clicks.jsonl")
EVENT_DIR = os.path.join(PROJECT_ROOT, "logs", "events")

# 这些 handler 命中时存一帧（报告页要「事件 ↔ 截图」一一对应）。
# 训练科目/比赛内这种每几秒一次的就不存，避免每轮几十张图。
SNAP_HANDLERS = {
    "handle_event_choice", "handle_boost_training", "handle_restaurant",
    "handle_park", "handle_street", "handle_arcade", "handle_shop",
    "handle_stock_pick", "handle_stock_result", "handle_skill_select",
    "handle_weekend", "handle_go_out", "handle_training_plan",
    "handle_result", "handle_match_result", "handle_prepare",
    "handle_register_player", "handle_vacation", "handle_rest_confirm",
}

# 游戏厅小游戏播动画期间，用这些「真实界面标记」判断动画结束了没。
# 注意不能放「星期 / 比赛」这类顶栏文字：小游戏画面本身也带顶栏（第N周 星期六）。
ARCADE_WAIT_MARKS = ("出行", "公园", "步行街", "游戏厅", "失败率", "决定", "休息",
                     "训练", "商店", "获得奖励", "点击屏幕继续", "跳过", "下一步",
                     "战术板", "赛程")

# 监控页（src/ui/MonitorTab.py）只认「域」这一级，不逐个 handler 维护文案。
# 新增 handler 只要模块不变就自动归类，不会因为漏配而显示成技术名。
UI_AREAS = {
    "popup": "弹窗",
    "training": "训练",
    "weekend": "周末",
    "match": "比赛",
    "entry": "入口",
    "fishing": "钓鱼",
}

# 监控页时间线保留的条数（UI 那边最多显示 8 条）。
UI_EVENT_LIMIT = 12

# 监控页「最近动作」详情列 = 本拍识别到的文字。
# 一帧 OCR 常有 20~40 条（体力、金币、按钮、顶栏都算），单行放不下，
# 取前几条去重后截断；保留顺序（OCR 的检测顺序），不排序 —— 排序会让
# 「第 N 周」「训练」这些关键界面词乱序，反而不像原界面。
UI_TEXT_LIMIT = 8
UI_TEXT_MAXLEN = 50


def summary_texts(texts, limit=UI_TEXT_LIMIT, max_len=UI_TEXT_MAXLEN):
    """把本拍 OCR 结果压成一行短文案，给监控页时间线用。

    只做展示，不参与业务判断；`texts` 为空或元素没有 name 都容错。
    """
    seen, items = set(), []
    for t in texts or []:
        name = (getattr(t, "name", "") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        items.append(name)
        if len(items) >= limit:
            break
    text = " / ".join(items)
    if len(text) > max_len:
        text = text[:max_len - 1] + "…"
    return text


class TrainingFlowMixin:

    def run(self):
        self._steps = 0
        # MCP / GUI 双入口：定产物目录。MCP 的 runner 已经 reset+verify 过，
        # GUI/CLI 走这里自己恢复基线（失败只告警，不打断用户手动起的任务）。
        self.begin_attempt()
        if not self.mcp_driven:
            if not self.recover_to_lobby():
                self.log_warning("GUI 运行：自动回大厅失败，按当前界面继续"
                                 "（MCP 运行遇到同样情况会直接判失败）")
            else:
                self.log_info("GUI 运行：已回到大厅，开始跑流程")
        STOP.ensure_started()
        self.log_info(f"紧急停止热键：{STOP.label or '（注册失败，F8/F12 轮询兜底已启用）'}")
        # 代码版本戳：确认跑的是哪份 parsers.py（圈识别调效果时排查过「改了不生效」）
        try:
            from src.tasks.training import parsers as _p
            self.log_info(f"parsers: {_p.__file__} v{getattr(_p, 'CODE_VERSION', '?')}")
        except Exception:                        # noqa: BLE001
            pass
        interaction = self.apply_user_idle_wait(self.config.get("空闲等待(秒)"))
        # 等待用户空闲可能很久：把紧急停止挂进去，否则按了停止键要等用户停手才生效
        if interaction is not None and hasattr(interaction, "abort_check"):
            interaction.abort_check = STOP.event.is_set
        # 流派只由队长决定（用户 2026-10-29）：配置里填了队长就先定流派，
        # 留空才等第一个训练日点日历解析一次。
        # 2026-10-30 修正：**只有从大厅开新一局时才套用配置队长**。
        # 中途接手别人的局时，实际队长可能不是配置里那个（实跑：配置春澄永远=扭蛋流，
        # 但那一局队长是闪堂电=训练流 → 周末计划按扭蛋流走错）。
        self.reset_style()
        if self.is_lobby():
            self.apply_configured_captain()
        else:
            self.log_info("非大厅开局 → 流派以游戏内队长/日历为准，不套用配置队长")
        # 运行期状态每次开局都要清（2026-10-29 实机：连开第二把时任务秒退，
        # 因为 _register_stage 还停在上次的 "back"，run() 的终点判据立刻命中）
        self.reset_run_state()
        self.log_info(f"流派：{self.style}"
                      + ("" if self.style_known else "（待日历识别队长）"))
        max_steps = int(self.config.get("单局最大操作数"))
        interval = float(self.config.get("操作间隔(秒)"))
        stuck_seconds = float(self.config.get("卡死判定(秒)"))
        stuck_limit = max(3, int(stuck_seconds / max(interval, 0.05)))
        last_sig, stuck = None, 0
        self._last_dump = 0.0

        while self._steps < max_steps:
            # 业务侧主动喊停（例如指定队长不在池子里）：下一拍就退出，不再往下点。
            if getattr(self, "_abort_reason", None):
                self.log_error(f"主动停止：{self._abort_reason}", notify=True)
                return False
            if STOP.consume():
                self.log_warning(f"按下紧急停止键（{STOP.last_press or STOP.label}）→ 立即中断，鼠标已释放",
                                 notify=True)
                return False
            if not self._enabled:
                self.log_info("任务已停止")
                return False
            self._steps += 1

            # 本拍只用这一帧：识别、存证、调试包都从它来。
            # 坑（2026-10-30 用户反馈「截图没截到正确时间点」）：snap_event 原来在
            # handler 返回后读 self.frame，那时采集线程已经给出**点击之后**的新帧，
            # 存下来的是「已经翻页」的画面，报告里自然对不上事件。
            frame = self.frame
            self._tick_frame = frame
            self._tick_clicks = []
            texts = self._ocr_region(frame=frame)
            # 本拍 OCR 结果留给 top_bar()：**裁剪顶栏再 OCR 会把体力条的「/」吃掉**
            # （`0/130` → `301130`），STAMINA_RE 要求有斜杠 → stamina 恒为 None，
            # 「精力不够就改选不耗精力那张」的规则全部失效（2026-10-31 餐厅实跑）。
            # 整帧这份是免费的，直接复用。
            self._tick_texts = texts
            if not texts:
                self.log_debug("本帧没有识别到文字，等待")
                self.sleep(0.3)
                continue

            if self.is_loading(texts):
                self.sleep(0.3)
                continue

            # 游戏厅买完小游戏：小游戏会**自己打完**，只等动画结束就行（用户 2026-10-29
            # 口径），不要点、也不要算卡死。等画面重新出现已知界面标记再交回 handler。
            if self.arcade_still_playing(texts):
                if time.time() - self._arcade_wait_since > 300:
                    self.log_warning("游戏厅小游戏 5 分钟还没结束 → 继续按普通流程处理")
                    self._arcade_wait_since = 0.0
                else:
                    stuck, last_sig = 0, None      # 动画期间不算卡死
                    self.sleep(0.5)
                    continue
            elif getattr(self, "_arcade_wait_since", 0.0):
                # 动画刚结束：5 秒看门狗从这一刻重新计时，别拿买小游戏之前的时间戳判卡死
                self._arcade_wait_since = 0.0
                self._last_click_at = time.time()
                stuck, last_sig = 0, None

            # 界面指纹：同一页面每拍都一样，用来判「卡住了没」
            sig = self._signature(texts)
            if sig == last_sig:
                stuck += 1
            else:
                stuck, last_sig = 0, sig

            # ---- 5 秒没有任何点击、界面文字也没变 → 卡住，存证后点中央继续 ----
            # 只看「签名没变」会误判：剧情 ▼ 连点 5 秒时文字确实不变，但点击在推进。
            # 所以两个条件都要满足：既没有点击事件，界面也没变。
            if self._stalled(sig, last_sig, stuck_seconds):
                self._stuck_rounds += 1
                self.dump_debug(texts, f"stall{int(stuck_seconds)}s", frame)
                self.log_warning(
                    f"{stuck_seconds:.0f} 秒没有点击、界面也没变化"
                    f"（上次动作：{self._last_state}）→ 已存证 logs/debug/，点击屏幕中央继续",
                    notify=True)
                if self._stuck_rounds >= 3:
                    self.log_error("连续 3 轮「点了也不动」→ 停止任务，避免一直卡在同一界面")
                    return False
                self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=interval)
                stuck, last_sig = 0, None
                continue

            # ---- 点 3 次没变化 → 立刻进入 debug（用户 2026-10-26 口径）----
            # 不等到 10 秒卡死判定，也不让 handler 一直"命中但无效"地空转。
            if self._sig_after_click is not None:
                if sig == self._sig_after_click:
                    self._no_change += 1
                else:
                    self._no_change = 0
                    self._sig_after_click = None
                    self._stuck_rounds = 0

            if self._no_change >= 3:
                self._stuck_rounds += 1
                self.dump_debug(texts, f"nochange{self._no_change}", frame)
                self.log_warning(
                    f"连续 {self._no_change} 次点击界面没变化（上次动作：{self._last_state}）"
                    f"→ 已存证 logs/debug/，点击屏幕中央重试")
                if self._stuck_rounds >= 3:
                    self.log_error("已经 3 轮「点不动」→ 停止任务，避免一直卡在同一界面")
                    return False
                self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=interval)
                self._no_change = 0
                self._sig_after_click = sig
                continue

            # ---- 卡死检测必须**优先于所有 handler** ----
            # 否则会出现「handler 一直命中、但点了完全没反应」的死循环：
            # 例如剧情页的「跳过」是灰的，handle_dialog_continue 每拍都返回 True，
            # 于是连点 70+ 次、把 400 步全耗光（实训 2026-10-26）。
            if stuck >= stuck_limit:
                self.dump_unknown(texts, "stuck")
                self.log_warning(f"连续 {stuck + 1} 拍没变化 → 强制点一下继续")
                self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=interval)
                stuck, last_sig = 0, None
                continue

            # ---- 一局的终点：注册球员六步走完（点过左上角「«」返回大厅）才算跑完 ----
            # 2026-10-27 用户补充：打完训练赛 → 注册球员 → 编队 → 保存 → 回大厅，
            # 才是完整一局；旧版停在「注册球员」页就算完成，把最后 6 步留给玩家。
            at_board = (self.match_text(texts, "战术板") is not None
                        or self.match_text(texts, "首发") is not None
                        or self.match_text(texts, "保存编队") is not None)
            if getattr(self, "_register_stage", None) == "back" and not at_board:
                self.log_info("注册球员流程完成（已回大厅）→ 任务完成", notify=True)
                return True

            if self._handle_any(texts, frame):
                self.sleep(interval)
                continue

            # ---- 兜底：不认识的界面也**让流程继续走**，不要卡死 ----
            # 存证限流：同一段时间最多 3 秒存一张，避免动画期间刷出几十张图
            if stuck == 0 and time.monotonic() - self._last_dump > 3:
                self.dump_unknown(texts, "unknown")
                self._last_dump = time.monotonic()
                self.log_warning("没见过这个界面 → 已存证，点一下继续")

            # 兜底点击也要参与「点 3 次没变化」检测，否则不认识的界面
            # 会一直静默点中央，既不 debug 也不停（2026-10-26 公园详情页踩过）。
            self._sig_after_click = sig
            self._last_state = "unknown"
            # 点画面中央：对话页能推进、空白处能关弹窗、大部分过场也吃这一下
            self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=interval)

        self.log_warning(f"达到最大操作数 {max_steps}，停止")
        return False

    def reset_run_state(self):
        """每次 run() 开始时清掉「上一局遗留」的运行期状态。

        ok-script 会复用同一个 Task 实例，不清就会：
        - `_register_stage == "back"` → 第二把秒退「任务完成」（2026-10-29 实机）；
        - 商品/商店标记、卡死计数、快进标记带到新的一局。
        """
        self._register_stage = None
        self._reg_ticks = 0
        self._abort_reason = None
        self._force_rest = False
        self._shop_day = None
        self._shop_try_key = None
        self._shop_try_count = 0
        self._prematch_noshop_key = None
        self._shop_spend_all = False
        self._shop_seen = False
        self._arcade_wait_since = 0.0
        self._no_change = 0
        self._sig_after_click = None
        self._stuck_rounds = 0
        self._picked_subject = False
        self._picked_index = 0
        self._subject_confirmed_at = 0.0
        self._match_flags = {"manual_fixed": False, "fast_clicked": False,
                             "last_fast_at": 0.0}
        self._subject_dumps = 0
        self._skill_dumps = 0
        self._stock_dumps = 0
        # 选择笔记页的限流标记（2026-10-30：同一页重复点击）
        self._note_auto_done = False
        self._note_next_tries = 0
        # 钓鱼转盘：点一次后锁定 8 秒（后面是动画，界面文字不变）
        self._fishing_clicked_at = 0.0

    def snap_event(self, handler_name, texts, frame=None):
        """事件命中时存一帧 + 一份 OCR 摘要，供报告页「事件 ↔ 截图」配对。

        frame 必须是**本拍识别用的那一帧**（run() 里在 handler 之前取好）。
        不传才退回 self.frame —— 那时 handler 已经点过，可能已经是下一页，
        这就是 2026-10-30 用户反馈「截图没截到正确时间点」的原因。

        限流：同一秒只存一张；只存 SNAP_HANDLERS 里的 handler。
        """
        if handler_name not in SNAP_HANDLERS:
            return
        now = time.time()
        if now - getattr(self, "_last_snap_at", 0.0) < 1.0:
            return
        self._last_snap_at = now
        frame = self.frame if frame is None else frame
        if frame is None or not frame.any():
            return
        try:
            os.makedirs(EVENT_DIR, exist_ok=True)
            stamp = time.strftime("%m%d-%H%M%S", time.localtime(now))
            base = os.path.join(EVENT_DIR, f"{stamp}_{handler_name}")
            cv2.imwrite(base + ".jpg", frame, JPEG_PARAMS)
            names = [t.name.strip() for t in texts if t.name.strip()][:30]
            # 本拍的点击（handler 在这一拍里实际点了哪里）：报告「内容」列直接用它，
            # 不用再去日志里猜。
            clicks = list(getattr(self, "_tick_clicks", []))
            with open(base + ".json", "w", encoding="utf-8") as f:
                json.dump({"at": time.strftime("%Y-%m-%d %H:%M:%S",
                                               time.localtime(now)),
                           "handler": handler_name,
                           "texts": names,
                           "clicks": clicks},
                          f, ensure_ascii=False)
        except Exception as e:                   # noqa: BLE001
            self.log_debug(f"事件截图失败: {type(e).__name__}: {e}")

    @staticmethod
    def _signature(texts):
        """界面指纹：只看**文字内容**，不看位置。

        坑（2026-10-26）：之前把坐标也算进指纹，动画/滚动时文字位置抖动 20px 就变，
        结果每一拍都被当成"新界面"，疯狂存证、stuck 永远为 0。
        """
        return hash("|".join(sorted(t.name for t in texts)))

    # ---------- 点击流水 ----------

    def click(self, x=-1, y=-1, *args, **kwargs):
        """转发 ok 的 click，同时把每次点击写进 logs/clicks.jsonl。

        为什么要再包一层：ok 自带 click 只在传了 name 时才打坐标日志，而业务代码
        大多不传 name，事后无法复盘「第几秒点了哪里、当时是什么界面」。
        另外 run() 的「5 秒没点击」看门狗也依赖这里的时间戳。
        """
        at = time.time()
        self._last_click_at = at
        px, py = self._click_point(x, y)
        record = {
            "at": round(at, 3),
            "iso": time.strftime("%H:%M:%S", time.localtime(at)) + f".{int(at % 1 * 1000):03d}",
            "step": self._steps,
            "state": self._last_state or "",
            "name": kwargs.get("name") or "",
            "x": px,
            "y": py,
            "key": kwargs.get("key", "left"),
            "ok": True,
        }
        try:
            return super().click(x, y, *args, **kwargs)
        except Exception as e:                       # noqa: BLE001
            record["ok"] = False
            record["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            # 点击**完成**时刻才算「最近一次点击」：用户抢鼠标时 interaction 会在
            # click 里面暂停几秒，用开始时刻计时会让看门狗把这段让路误判成卡死。
            self._last_click_at = time.time()
            self._journal_click(record)
            # 记进本拍点击列表，snap_event 会把它写进事件 json（报告「内容」列）
            tick_clicks = getattr(self, "_tick_clicks", None)
            if tick_clicks is not None:
                tick_clicks.append({"name": record["name"], "x": px, "y": py})

    @staticmethod
    def _click_point(x, y):
        """把 Box / Box 列表 / 相对坐标换算成可读的像素点，只用于存证。"""
        if isinstance(x, (list, tuple)):
            x = x[0] if x else -1
        if hasattr(x, "width") and hasattr(x, "height"):
            return int(x.x + x.width / 2), int(x.y + x.height / 2)
        return x, y

    def _stalled(self, sig, last_sig, stuck_seconds, now=None):
        """卡住判据：界面指纹没变 **且** 超过 N 秒没有任何点击。

        为什么不是只看指纹：剧情 ▼ 连点 5 秒时文字完全不变，但每拍都有点击，
        流程是在推进的；那种页面绝不能判成卡死。
        """
        if not self._last_click_at or last_sig is None or sig != last_sig:
            return False
        return (now or time.time()) - self._last_click_at >= stuck_seconds

    @staticmethod
    def _journal_click(record):
        try:
            os.makedirs(os.path.dirname(CLICK_LOG), exist_ok=True)
            with open(CLICK_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:                            # noqa: BLE001
            pass

    def dump_debug(self, texts, tag, frame=None):
        """点不动的时候存一份「调试包」：截图 + OCR + 最近动作 + 线程。

        用户口径（2026-10-26）：连续点 3 次界面没变化就要开始 debug。
        产物在 logs/debug/<时间>_<tag>/，logs 已被 .gitignore 忽略。
        frame 同样要传**本拍识别那一帧**，否则存的是点击后的画面。
        """
        try:
            root = os.path.join(PROJECT_ROOT, "logs", "debug")
            folder = os.path.join(root, f"{time.strftime('%m%d-%H%M%S')}_{tag}")
            os.makedirs(folder, exist_ok=True)

            frame = self.frame if frame is None else frame
            if frame is not None:
                # 存 JPEG 不存 PNG：同一帧 PNG 约 2.4 MB、JPEG 约 0.36 MB，读图更快。
                # JPEG_PARAMS 是 q95 + 4:4:4（见文件顶部），保证颜色可复核。
                # 注意：运行时取色读的是内存里的 self.frame，与这里存盘格式无关。
                cv2.imwrite(os.path.join(folder, "frame.jpg"), frame, JPEG_PARAMS)
            with open(os.path.join(folder, "ocr.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(
                    f"{t.name}\t({t.x},{t.y},{t.width},{t.height})" for t in texts))
            with open(os.path.join(folder, "actions.txt"), "w", encoding="utf-8") as f:
                f.write(f"step={self._steps} last={self._last_state} "
                        f"no_change={self._no_change} rounds={self._stuck_rounds}\n")
                f.write("\n".join(self._recent_actions))
            with open(os.path.join(folder, "threads.txt"), "w", encoding="utf-8") as f:
                for th in threading.enumerate():
                    f.write(f"{th.name}\t{th.is_alive()}\n")
            self.log_info(f"调试包已保存：logs/debug/{os.path.basename(folder)}")
        except Exception as e:                       # noqa: BLE001
            self.log_debug(f"调试包保存失败: {type(e).__name__}: {e}")

    def _reset_handler_leave_states(self, hit_name):
        """命中 `hit_name` 后，把**其它** handler 声明的瞬时状态清回默认值。

        声明表在 `handlers/__init__.py: LEAVE_RESETS` —— 谁的状态谁声明，
        这里不认识任何具体 handler 名字，所以**加 handler 不用改本文件**。
        """
        from src.tasks.training.handlers import LEAVE_RESETS   # 局部导入，避开导入环

        for owner, defaults in LEAVE_RESETS.items():
            if owner == hit_name:
                continue
            for attr, default in defaults.items():
                setattr(self, attr, default() if callable(default) else default)

    def _handle_any(self, texts, frame=None):
        # 大厅子菜单（强化/招募/卡池…）：玩家自己点进去的，任务一律不动，
        # 也不要走「不认识的界面 → 点屏幕中央」兜底（2026-10-28 用户反馈：
        # 强化页被当成三选一点升级卡、招募页被 story_arrow 点了一下）。
        if self.is_lobby_subpage(texts):
            return True
        for handler in self.HANDLERS:
            try:
                if handler(self, texts):
                    self._last_state = handler.__name__
                    self._ui_event(handler, texts)
                    self.snap_event(handler.__name__, texts, frame)
                    self._recent_actions.append(f"step{self._steps} {handler.__name__}")
                    # 记录「点击之后应该看到的那一帧」的指纹，下一拍对比是否变化
                    self._sig_after_click = self._signature(texts)
                    self.log_debug(f"命中 {handler.__name__}（第 {self._steps} 步）")
                    # 各 handler 的瞬时状态由它自己声明（handlers/__init__.py: LEAVE_RESETS），
                    # 命中别的界面时自动清零。这里**不再用 handler 名字字符串判断** ——
                    # 那种写法每加一个 handler 都要回来改本函数（2026-11-01 重构）。
                    self._reset_handler_leave_states(handler.__name__)
                    return True
            except Exception as e:
                self.log_error(f"{handler.__name__} 异常: {type(e).__name__}: {e}")
                return True  # 吞掉这一拍，避免同一异常反复刷屏
        return False

    @property
    def no_captain_mode(self):
        """无队长模式：`指定队长` 为空且开关打开。

        用户 2026-10-31 口径：取消勾选队长 = 不接开局，只跑已经手动开好的局。
        开关 `无队长不接开局` 默认 True；回归桩（tools/check_handlers.py）关掉它，
        这样测试仍然按旧的「自动选第一个」流程跑。
        """
        if not self.config.get("无队长不接开局"):
            return False
        return not (self.config.get("指定队长") or "").strip()

    def stop_for_no_captain(self, stage):
        """无队长模式下遇到开局页面：不动作，直接让 run() 停下。"""
        self._abort_reason = f"无队长模式：不接开局（遇到「{stage}」）"
        self.log_warning(
            f"无队长模式：只跑已有局，当前在「{stage}」→ 停止任务。"
            "勾选队长后才会自动开局（启程/选人/笔记/器材）。",
            notify=True)
        return True

    def _ui_event(self, handler, texts=None):
        """把本拍命中的 handler 写进 info，供「监控」页显示。

        只写三个键：action（当前动作）、style（流派）、timeline（最近动作）。
        不参与业务逻辑，写失败也不影响流程。

        时间线的 `detail` 是**本拍识别到的文字**（`summary_texts`），
        看得到「当时屏幕上有什么」，比只写 handler 名好复盘；
        步数/全名另存 `step` / `handler`（UI 暂不显示，排障时用）。
        """
        try:
            info = getattr(self, "info", None)
            if not isinstance(info, dict):
                return
            module = handler.__module__.rsplit(".", 1)[-1]
            area = UI_AREAS.get(module, "流程")
            name = handler.__name__.removeprefix("handle_")
            info["action"] = f"{area} · {name}"
            # 用 self.style：没识别队长时它自己会兜底成「训练流」，
            # 这才是脚本实际在跑的流派（旧版写「未定」，监控页就看不到真实值）
            info["style"] = self.style
            info["style_known"] = self.style_known
            events = info.get("timeline")
            if not isinstance(events, list):
                events = []
            events.insert(0, {
                "time": time.strftime("%H:%M:%S"),
                "event": area,
                "detail": summary_texts(texts),
                "result": "成功",
                "step": self._steps,
                "handler": name,
            })
            del events[UI_EVENT_LIMIT:]
            info["timeline"] = events
        except Exception as e:                       # noqa: BLE001
            self.log_debug(f"写入监控信息失败: {type(e).__name__}: {e}")

    def arcade_still_playing(self, texts):
        """游戏厅小游戏还在播动画吗？

        买完小游戏后画面只有顶栏、没有按钮（角色站在球场中间那种）。
        用户 2026-10-29 口径：小游戏会自己打完，**一直等到动画结束**就行。
        这里用「有没有重新出现已知界面标记」判断，不按时间猜。
        """
        if not getattr(self, "_arcade_wait_since", 0.0):
            return False
        names = "".join(t.name for t in texts)
        return not any(k in names for k in ARCADE_WAIT_MARKS)

    @property
    def style(self):
        """当前流派 —— 由队长决定（见 docs/RULES.md 第 0 节）。

        快速接手流程：进训练时这个变量是空的 → 点一次日历解析队长流派。
        全自动流程（另一条流程，尚未实装）：开局自己把流派写进 `_style`。
        还没定下来时按「训练流」跑（最保守）。
        """
        return getattr(self, "_style", None) or "训练流"

    @property
    def style_known(self):
        """流派定了没有？没定就要去开日历认队长。"""
        return bool(getattr(self, "_style", None))

    def reset_style(self):
        """清空流派（新的一局 / 接管一局）：等日历解析队长。"""
        self._style = None
        self._style_name = None
        self._style_probe_tries = 0
        self._style_probe_at = 0.0

    def apply_configured_captain(self):
        """配置里填了「指定队长」→ 直接定流派，**不点日历**。

        2026-10-30 用户反馈「星期二为什么开日历了，没这个设置吧」：
        旧代码的「指定队长」只用于在选择队长页挑卡（handlers/entry.py），
        从没写进 `_style`；而 `_style` 的唯一赋值点是日历解析
        （handlers/training.py:handle_training_plan）。于是只要队长不是从日历读的，
        第一个训练日就必然触发 handle_style_probe 去点左上角日历，
        跟「配置里填了队长就开局有流派」的注释完全不一致。

        现在：填了队长就查表定流派；查不到（SR/R 或 OCR 名不匹配）才回落到日历。
        """
        name = (self.config.get("指定队长") or "").strip()
        if not name:
            return False
        style = self.style_for_name(name)
        if not style:
            self.log_warning(
                f"配置的队长「{name}」不在角色表（STYLE_BY_NAME）→ 仍按日历识别流派")
            return False
        self._style = style
        self._style_name = name
        self.log_info(f"流派：{style}（配置指定队长「{name}」，跳过日历）")
        return True

    def weekend_priority(self, day=None, st=None):
        """今天该去的地点（有序）。

        **判断已搬到 `training/places/map.py`**（地图 = 决策模块）：
        行程表、扭蛋流的「先抽扭蛋再打工」、两个开关过滤都在那里。
        这里保留旧名，让现有调用点不用改。
        """
        return self.today_places(day, st)

    def rest_threshold(self, day=None):
        """今天「精力低于多少就休息」。

        用户 2026-10-30：**周四**体力低于 75 就休息 —— 把体力留给周五的周末
        （周五扭蛋流要打工/抽扭蛋）。原来的周五特殊阈值（训练流以外 55、
        商店流 80）已取消，周五回到配置里的「休息体力阈值」。
        """
        base = int(self.config.get("休息体力阈值"))
        if day == "四":
            return max(base, THURSDAY_REST_THRESHOLD)
        return base
