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

    # 科目预览打分（用户 2026-10-27 定稿权重）
    # 队友 1 / 装备 1 / 经理 2.1 / 金框 2.2 / 彩框 2.3
    #
    # 为什么三者同档（2.x）且 彩框 > 金框 > 经理：
    #   - 实测 118 次训练的边际属性收益：金框 +32~35、经理 +9~11，档内排序与数据一致；
    #   - 金框永远不可能单独出现（外圈亮 = 装备/经理经验满），总是和「装备 1.0」一起计分，
    #     所以档内 0.1 的差距不会造成噪声问题：真正被比较的是「金框装备 3.2」对「经理 2.1」；
    #   - 旧值 1.1 / 1.2 会让「金框装备 3.2」只比「队员+经理 3.1」高 0.1，等于平局。
    # 关键口径：**金框/彩框指训练科目按钮外面那一圈**，不是底部预览区的器材卡。
    # 按卡上的金框算会和「装备」重复计分（框本来就是装备/经理经验满了才点亮），
    # 所以装备只按张数算一次，框只看按钮外圈。
    #
    # 经理 = 2.0 的推导（2026-10-27 实跑日志）：
    #   - 选中带经理的科目 17 次，触发「加强训练」9 次（≈53%），不是每次都触发；
    #   - 加强训练二选一总选高档：+36（70~100% 成功率），期望 36/36/32/29/25… 均值 31；
    #   - 折算每次"选到经理"的期望收益 ≈ 31 × 0.53 ≈ 16.5 点属性，
    #     约等于一次普通训练的基础收益（+12 量级）的 1.4 倍；
    #   - 但玩家实际感知是"白拿一次 36"，且这是唯一能稳定换到高档训练的入口，
    #     所以取 2.1（用户 2026-10-27 定）：比折算值略高，压过 2 个队友，
    #     但压不过「3 个队友 + 装备」的科目。
    W_MATE = 1.0
    W_EQUIP = 1.0
    W_MANAGER = 2.1
    W_GOLD = 2.2
    W_COLOR = 2.3

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
                + r["gold"] * cls.W_GOLD
                + r["color"] * cls.W_COLOR)

    @staticmethod
    def subject_rank(score, is_recommended, attr_value, y):
        """科目排序键（用户 2026-10-26 口径）。

        分数 → 队长推荐属性 → 当前数值**低**的（成长空间大）→ 屏幕上靠前的（兜底）。
        读不到数值的属性用 10^6 垫底，不能靠「读不到」白捡便宜。
        """
        value = attr_value if attr_value is not None else 10 ** 6
        return (score, 1 if is_recommended else 0, -value, -y)

    # 六维属性名：用来把「全属性+5」换算成真实的属性总量
    ATTR_NAMES = ("投射", "弹跳", "运控", "体能", "防守", "意识")
    # 精力只值一点钱：用户 2026-10-26「只回 10 精力，不如一点属性」
    STAMINA_WEIGHT = 0.2

    @classmethod
    def _effect_ev(cls, effect_texts):
        """从效果文本里算**真实属性总量**（用户 2026-10-27 口径）。

        关键：数字后面跟的说明决定倍率——「全属性+5」是六项各 +5 = **30 点**，
        「投射+3」才是 3 点。旧实现只看数字大小，会出现
        「全属性+5(30)」输给「精力+10(实际不值)」这种明显错误。

        规则：
        - 前缀含「全属性 / 所有属性 / 全体」→ ×6；
        - 前缀里有 n 个属性名（「投射+2，弹跳+3」）→ 分别按 1 倍累加；
        - 精力 → ×0.2（纯回精力的选项在挑选阶段还会被排除）；
        - 带概率的（「50%概率 +5」）按 收益×概率 计，并与确定收益取较大值。
        """
        joined = "".join(t.name for t in effect_texts).replace(" ", "")
        certain = 0.0
        best_prob = 0.0
        for m in re.finditer(r"(?:(\d{1,3})%概率)?[^+\d]{0,8}\+(\d{1,3})", joined):
            p = int(m.group(1)) if m.group(1) else 100
            v = int(m.group(2))
            # 说明文字在「+N」前面，就在这次匹配里（不能看 m.start() 之前，
            # 那个位置是空字符串，「全属性+5」会算成 5 而不是 30）
            prefix = m.group(0).split("+")[0]
            if ("全属性" in prefix or "所有属性" in prefix or "全体" in prefix
                    or "六维" in prefix):
                mult = len(cls.ATTR_NAMES)
            else:
                hits = sum(1 for a in cls.ATTR_NAMES if a in prefix)
                if hits:
                    mult = hits
                elif "精力" in prefix:
                    mult = cls.STAMINA_WEIGHT
                else:
                    mult = 1
            ev = v * mult
            if p >= 100:
                certain += ev
            else:
                best_prob = max(best_prob, ev * p / 100.0)
        if certain == 0.0 and best_prob == 0.0:
            return None
        return max(certain, best_prob)
