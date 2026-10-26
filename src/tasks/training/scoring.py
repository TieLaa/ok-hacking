"""纯打分函数，输入结构化数据，不碰 self 之外的副作用。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
"""

import random
import re
import time

import os
import threading
from collections import deque

import cv2
import numpy as np

from src.tasks.ui import Defs  # noqa: F401
from src.tasks.training.constants import JPEG_PARAMS  # noqa: F401


class TrainingScoringMixin:

    # 科目预览打分（用户 2026-10-26 简化版权重）
    # 队友 1 / 道具 1 / 经理 1 / 黄框 +2.1 / 彩框 +3.1
    # （用户分不清「经理暴击」和「道具暴击」，所以不再单独给暴击项加权。
    #   2026-10-27 用户实测：有框时暴击训练加的属性很多，
    #   黄框/彩框不再是「同分微调」，各自 2.1 / 3.1，能压过队友数量。）
    W_MATE = 1.0
    W_EQUIP = 1.0
    W_MANAGER = 1.0
    W_GOLD_CRIT = 2.1
    W_COLOR_CRIT = 3.1

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
    def subject_rank(score, is_recommended, attr_value, y):
        """科目排序键（用户 2026-10-26 口径）。

        分数 → 队长推荐属性 → 当前数值**低**的（成长空间大）→ 屏幕上靠前的（兜底）。
        读不到数值的属性用 10^6 垫底，不能靠「读不到」白捡便宜。
        """
        value = attr_value if attr_value is not None else 10 ** 6
        return (score, 1 if is_recommended else 0, -value, -y)

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
