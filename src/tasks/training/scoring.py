"""纯打分函数，输入结构化数据，不碰 self 之外的副作用。

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


class TrainingScoringMixin:

    # 科目预览打分（用户 2026-10-26 简化版权重）
    # 队友 1 / 道具 1 / 经理 1 / 黄框 +0.1 / 彩框 +0.2
    # （用户分不清「经理暴击」和「道具暴击」，所以不再单独给暴击项加权，
    #   只把黄框、彩框当作同分时的小加成。）
    W_MATE = 1.0
    W_EQUIP = 1.0
    W_MANAGER = 1.0
    W_GOLD_CRIT = 0.1
    W_COLOR_CRIT = 0.2

    @classmethod
    def empty_preview(cls):
        return {"names": [], "mates": 0, "manager": 0, "equip": 0, "full": 0,
                "gold": 0, "crit": False, "color": 0, "manager_crit": 0}

    @classmethod
    def subject_score(cls, r):
        """按权重算总分（全部是正分，直接排序）。"""
        if not r:
            return 0.0
        return (r["mates"] * cls.W_MATE
                + r["equip"] * cls.W_EQUIP
                + r["manager"] * cls.W_MANAGER
                + r["gold"] * cls.W_GOLD_CRIT
                + r["color"] * cls.W_COLOR_CRIT)

    @staticmethod
    def _effect_ev(effect_texts):
        """从效果文本里算期望值：例如「精力+10」=10，'50%概率 精力+30'=15。

        找不到可解析的 +N 返回 None。
        """
        joined = "".join(t.name for t in effect_texts).replace(" ", "")
        best = None
        for m in re.finditer(r"(?:(\d{1,3})%概率)?[^+\d]{0,8}\+(\d{1,3})", joined):
            p = int(m.group(1)) if m.group(1) else 100
            v = int(m.group(2))
            ev = v * p / 100.0
            if best is None or ev > best:
                best = ev
        return best
