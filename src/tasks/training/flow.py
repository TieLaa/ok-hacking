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

from src.tasks.ui import Defs  # noqa: F401
from src.tasks.training.constants import (FRIDAY_REST_OTHER, FRIDAY_REST_SHOP,
                                          JPEG_PARAMS, PROJECT_ROOT, STYLE_AUTO,
                                          STYLE_PLACES, WEEKEND_PLAN)  # noqa: F401
from src.util.emergency_stop import STOP

# 点击流水：一行一个 JSON 事件（时间戳 / 步数 / 当时界面 / 坐标）。
# 放在 logs/ 下，已被 .gitignore 忽略；任务结束后用它复盘「第几秒点了哪里」。
CLICK_LOG = os.path.join(PROJECT_ROOT, "logs", "clicks.jsonl")

# 游戏厅小游戏播动画期间，用这些「真实界面标记」判断动画结束了没。
# 注意不能放「星期 / 比赛」这类顶栏文字：小游戏画面本身也带顶栏（第N周 星期六）。
ARCADE_WAIT_MARKS = ("出行", "公园", "步行街", "游戏厅", "失败率", "决定", "休息",
                     "训练", "商店", "获得奖励", "点击屏幕继续", "跳过", "下一步",
                     "战术板", "赛程")


class TrainingFlowMixin:

    def run(self):
        self._steps = 0
        STOP.ensure_started()
        self.log_info(f"紧急停止热键：{STOP.label or '（注册失败，F8/F12 轮询兜底已启用）'}")
        interaction = self.apply_user_idle_wait(self.config.get("空闲等待(秒)", 3))
        # 等待用户空闲可能很久：把紧急停止挂进去，否则按了停止键要等用户停手才生效
        if interaction is not None and hasattr(interaction, "abort_check"):
            interaction.abort_check = STOP.event.is_set
        max_steps = int(self.config.get("单局最大操作数", 400))
        interval = float(self.config.get("操作间隔(秒)", 0.8))
        stuck_seconds = float(self.config.get("卡死判定(秒)", 5))
        stuck_limit = max(3, int(stuck_seconds / max(interval, 0.05)))
        last_sig, stuck = None, 0
        self._last_dump = 0.0

        while self._steps < max_steps:
            if STOP.consume():
                self.log_warning(f"按下紧急停止键（{STOP.last_press or STOP.label}）→ 立即中断，鼠标已释放",
                                 notify=True)
                return False
            if not self._enabled:
                self.log_info("任务已停止")
                return False
            self._steps += 1

            texts = self._ocr_region()
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
                self.dump_debug(texts, f"stall{int(stuck_seconds)}s")
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
                self.dump_debug(texts, f"nochange{self._no_change}")
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

            if self._handle_any(texts):
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

    def dump_debug(self, texts, tag):
        """点不动的时候存一份「调试包」：截图 + OCR + 最近动作 + 线程。

        用户口径（2026-10-26）：连续点 3 次界面没变化就要开始 debug。
        产物在 logs/debug/<时间>_<tag>/，logs 已被 .gitignore 忽略。
        """
        try:
            root = os.path.join(PROJECT_ROOT, "logs", "debug")
            folder = os.path.join(root, f"{time.strftime('%m%d-%H%M%S')}_{tag}")
            os.makedirs(folder, exist_ok=True)

            frame = self.frame
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

    def _handle_any(self, texts):
        # 大厅子菜单（强化/招募/卡池…）：玩家自己点进去的，任务一律不动，
        # 也不要走「不认识的界面 → 点屏幕中央」兜底（2026-10-28 用户反馈：
        # 强化页被当成三选一点升级卡、招募页被 story_arrow 点了一下）。
        if self.is_lobby_subpage(texts):
            return True
        for handler in self.HANDLERS:
            try:
                if handler(self, texts):
                    self._last_state = handler.__name__
                    self._recent_actions.append(f"step{self._steps} {handler.__name__}")
                    # 记录「点击之后应该看到的那一帧」的指纹，下一拍对比是否变化
                    self._sig_after_click = self._signature(texts)
                    self.log_debug(f"命中 {handler.__name__}（第 {self._steps} 步）")
                    # 用名字判断而不是 `is TrainingTask.handle_x`：
                    # 拆分到 mixin 后类引用会循环 import，名字更稳。
                    name = handler.__name__
                    if name != "handle_prepare":
                        self._skip_tries = 0
                    if name != "handle_in_match":
                        self._match_flags = {"manual_fixed": False, "fast_clicked": False, "last_fast_at": 0.0}
                    if name != "handle_subject_select":
                        self._picked_subject = False
                        self._picked_index = 0
                    return True
            except Exception as e:
                self.log_error(f"{handler.__name__} 异常: {type(e).__name__}: {e}")
                return True  # 吞掉这一拍，避免同一异常反复刷屏
        return False

    @staticmethod
    def is_label(name):
        """浮层/角标文字（「划船比赛！」这种），只是视觉提示，点了没反应。"""
        s = (name or "").strip()
        if not s:
            return False
        return s.endswith("！") or s.endswith("!") or "加成" in s or "比赛！" in s

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

    def exit_page(self, after_sleep=0.45):
        """点右上角的返回箭头退出当前页面。

        坐标来自 2026-10-26 实测帧（2418x1360）：返回箭头是顶栏最右边那个
        「↩」按钮，按钮中心约 (0.945w, 0.040h)。旧值 (0.958, 0.060) 落在按钮
        右下角的圆角外，点十几次都不关页面（扭蛋页实测）。
        坑：左上角 (70,55) 那个按钮在训练日会打开「训练计划」日历，不是返回。
        """
        self.click(int(self.width * 0.945), int(self.height * 0.040), after_sleep=after_sleep)

    @property
    def style(self):
        """当前流派：识别到的队长流派 > 配置里手动锁定的流派 > 训练流兜底。

        用户 2026-10-29：点开始训练后这个值是空的，打开日历读到队长后写进来，
        默认按队长的流派跑；配置里选具体流派 = 手动锁定，不再去读日历。
        """
        return (getattr(self, "_style", None)
                or self.declared_style()
                or "训练流")

    def declared_style(self):
        """配置里写死的流派；「自动（按队长）」或空 → None。"""
        v = (self.config.get("流派") or "").strip()
        return None if (not v or v == STYLE_AUTO) else v

    @property
    def style_known(self):
        """流派定了没有？没定就要去开日历认队长。"""
        return bool(getattr(self, "_style", None) or self.declared_style())

    def reset_style_probe(self):
        """新的一局（点了开始训练）：流派重新识别 —— 队长可能换了。"""
        self._style = None
        self._style_name = None
        self._style_probe_tries = 0
        self._style_probe_at = 0.0

    def weekend_priority(self, day=None):
        """今天该去的地点（顺序 = 优先级）。

        用户 2026-10-29 口径（见 docs/RULES.md 1.2）：
        - 周六：训练流原设定（步行街 → 游戏厅 → 公园）；其他流派只去餐厅打工；
        - 周日：训练流→公园、商店流→餐厅、扭蛋流→步行街、小游戏流→游戏厅。

        「商店」是周末首页的按钮、不是地图上的地点，这里要过滤掉；
        步行街 / 游戏厅仍是显式开关（没验证过内页，先别默认乱进）。
        """
        style = self.style
        if day is None:
            day = (self.weekend_state().get("date") or (None, None))[1]
        if day == "六" and style != "训练流":
            places = ["餐厅"]
        else:
            places = list(WEEKEND_PLAN.get(
                (day, style), STYLE_PLACES.get(style, STYLE_PLACES["训练流"])))
        places = [p for p in places if p != "商店"]
        if not self.config.get("允许进游戏厅", True):
            places = [p for p in places if p != "游戏厅"]
        if not self.config.get("允许进步行街", True):
            places = [p for p in places if p != "步行街"]
        # 兜底：开关全关/表里没这个组合时总得花掉一天，退化去公园。
        # 注意不是「每个计划都追加公园」—— 周日扭蛋流只去步行街，逛完这一天就结束，
        # 再跑一趟公园属于计划外消耗（用户 2026-10-29 只给了一个地点）。
        if not places:
            places = ["公园"]
        return places

    def rest_threshold(self, day=None):
        """今天「精力低于多少就休息」。

        用户 2026-10-29：周五是周末前的最后一个训练日 ——
        训练流不变（配置里的休息体力阈值），其他流派留 55 点给周末，
        商店流周末要逛店 + 打工，留 80 点。
        """
        base = int(self.config.get("休息体力阈值", 40))
        if day != "五" or self.style == "训练流":
            return base
        return FRIDAY_REST_SHOP if self.style == "商店流" else FRIDAY_REST_OTHER

    def low_stamina(self, stamina_state):
        current = stamina_state.get("stamina")
        limit = self.rest_threshold(stamina_state.get("day"))
        return current is not None and current < limit
