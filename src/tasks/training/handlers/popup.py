"""弹窗 / 抽奖 / 休息确认。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
"""

import random
import re
import time

import cv2
import numpy as np

from src.tasks.ui import Defs  # noqa: F401
from src.tasks.training.constants import JPEG_PARAMS  # noqa: F401


class PopupHandlers:

    def handle_rest_confirm(self, texts):
        """「休息需要一整天，可恢复体力，是否要休息？」确认框：点「确定」。

        必须排在 handle_popup 之前。旧逻辑把「取消」当通用弹窗关闭词，
        结果点了休息→确认框→点取消→再点休息，永远休息不了（实训 2026-10-26）。
        """
        if self.match_text(texts, Defs.REST_CONFIRM_TEXTS) is None:
            return False
        # 按钮是 [取消] [确认]；**不能**整词匹配「休息」——左上角标题就叫「休息」，
        # 点了等于点对话框标题，什么都不发生（2026-10-26 实训卡死）。
        target = self.exact_text(texts, ("确定", "确认"))
        if target is None:
            bottom = [t for t in texts
                      if t.name.strip() == "休息"
                      and (t.y + t.height / 2) / self.height > 0.62]
            target = bottom[0] if bottom else None
        if target is not None:
            self.click_box(target, after_sleep=0.9)
            self.log_info(f"休息确认：点「{target.name.strip()}」")
            return True
        return False

    def handle_resume_training(self, texts):
        """大厅「训练确认」弹窗：继续上次没跑完的训练。

        实机（2026-10-26 用户截图）：标题「训练确认」+ 主角名 + 目标
        「赢得与XXX的比赛」，按钮是 [删档]（青色，左）[继续训练]（黄色，右）。
        必须点「继续训练」，**绝不能点「删档」**（会清掉这一局）。
        这个弹窗盖在大厅上，背景还能 OCR 到「赛事 / 社团GO / 训练中」，
        所以优先级要排在 handle_lobby 前面，否则会被当成大厅反复点「训练」。
        """
        if self.match_text(texts, "训练确认") is None:
            return False
        # 想开新的一局：先删掉上一局的存档（旧局已经跑完，删档不会误伤进度）
        if self.config.get("新一局删档", False):
            wipe = self.exact_text(texts, ("删档",))
            if wipe is not None:
                self.click_box(wipe, after_sleep=0.9)
                confirm = self.exact_text(self._ocr_region(), ("确定", "确认"))
                if confirm is not None:
                    self.click_box(confirm, after_sleep=0.9)
                self.log_info("训练确认：删档 → 开新的一局")
                return True
        target = self.match_text(texts, "继续训练")
        if target is not None:
            self.click_box(target, after_sleep=0.9)
            self.log_info("训练确认：继续上次训练")
            return True
        # 认不出文字时用实测相对坐标（黄色主按钮中心）
        self.click(int(self.width * 0.65), int(self.height * 0.75), after_sleep=0.9)
        self.log_warning("训练确认：没认出「继续训练」，按固定坐标点黄色按钮")
        return True

    def handle_popup(self, texts):
        """公告 / 领奖 / 月卡 / 提示 / 「点击屏幕继续」：先关掉，否则什么都点不动。

        「点击屏幕继续」= 整屏任意位置可点（训练日开场的「新目标」弹窗就是这种），
        所以点**屏幕中央**最稳 —— 点它那行文字反而容易碰到下面的「训练/休息」。
        """
        if self.match_text(texts, "点击屏幕继续") is not None:
            self.click(int(self.width * 0.5), int(self.height * 0.4), after_sleep=0.35)
            self.log_info("弹窗：点击屏幕继续")
            return True
        target = self.match_text(texts, ("今日不再提示", "关闭"))
        if target:
            self.click_box(target, after_sleep=0.35)
            self.log_info(f"关闭弹窗「{target.name}」")
            return True
        # 「取消」只在**没有确定按钮**的纯提示框里点；有确定按钮的确认框
        # 交给具体 handler（休息/购买），否则会把用户想做的操作取消掉。
        if self.match_text(texts, "确定") is None:
            cancel = next((t for t in texts if t.name.strip() == "取消"), None)
            if cancel is not None:
                self.click_box(cancel, after_sleep=0.35)
                self.log_info("关闭弹窗「取消」")
                return True
        # 抽卡/活动横幅：只有「立即前往」和右上角一个 X，没有「关闭」文字。
        # 不处理的话兜底会点屏幕中央，正好点进抽卡页（实训 2026-10-26）。
        if self.match_text(texts, "立即前往") is not None:
            self.click(int(self.width * 0.89), int(self.height * 0.17), after_sleep=0.6)
            self.log_info("关闭活动横幅")
            return True
        return False

    def handle_draw(self, texts):
        """大厅「幸运大抽奖」转盘：点「转一转」抽一次，结果交给弹窗处理。"""
        if self.match_text(texts, ("幸运大抽奖", "普通技能", "强力技能")) is None:
            return False
        target = self.match_text(texts, "转一转")
        if target is not None:
            self.click_box(target, after_sleep=1.2)
            self.log_info("抽奖：转一转")
        else:
            self.click(int(self.width * 0.5), int(self.height * 0.8), after_sleep=1.0)
            self.log_info("抽奖：点结果继续")
        return True
