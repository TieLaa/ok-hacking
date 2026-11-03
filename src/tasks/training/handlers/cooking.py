"""角色天赋特殊事件「做饭」：**请选择要使用的食材**（三选一）。

2026-11-03 实机（第 1 周 星期四，精力 97/130，零用钱 150）：

    标题「请选择要使用的食材」，三张竖卡横排，标题条按品质上色：

        隔夜饭             蓝   0 零用钱   「能吃，没写保质期就是没过期！」
        菜市场的普通食材   紫   10        「店长说卖完这筐就回老家！」
        珍稀食材           金   20        「最高端的食材往往使用最朴素的烹饪方式！」

    底部黄色「确定」，左下角「能力详情」，右上角倍速/历史/菜单/返回。

用户口径（2026-11-03）：**默认 金 > 紫 > 蓝**（彩色按更通用的
彩 > 金 > 紫 > 蓝 处理）；同品质再比队友头像数量（队友多的优先，
有队友优先于没队友）。这一页三张卡都没有队友头像，所以实际就是按品质
选最贵的那张 —— 金「珍稀食材」。

为什么不能按名字/价格选：
- 名字 OCR 会**截断最后一个字**（实机帧读到「隔夜…」「菜市场的普通食…」）；
- 价格是**品质的副产物**（蓝 0 / 紫 10 / 金 20），但别的天赋事件不一定这样；
- 判据只看颜色，和扭蛋/游戏厅共用 `card_quality_at` 的色相阈值。

旧版没有这个 handler：OCR 认得全部文字，但没有 handler 认领 →
每拍落 `unknown` 兜底点屏幕中央（紫色卡默认被选中，白送 10 零用钱的那张）。

同一事件的第二页是转盘（「开始做饭！」+「转一转」）：按钮和钓鱼那边**位置、
文案都一样**，所以归 `handlers/wheel.py: handle_spin_wheel` 统一处理，
本文件只管食材三选一。
"""

import time

from src.tasks.ui import labels

# 同一页重复存证的间隔（秒）：点卡 → 点确定要 2~3 拍，别把一页刷成十几张图。
# 注意：handler 是**函数**（注册表里按 `handler(self, texts)` 调），不是绑定方法，
# 所以这里不能用类属性（`self.UNMARK_INTERVAL` 会 AttributeError）。
UNMARK_INTERVAL = 10.0


class CookingHandlers:
    """做饭事件的食材三选一（mixin，方法通过 self 访问任务状态）。"""

    def handle_ingredient_select(self, texts):
        """「请选择要使用的食材」：按品质（彩>金>紫>蓝）→ 队友多 选一张，再点「确定」。"""
        if self.match_text(texts, labels.COOKING_TITLE) is None:
            return False
        cards = self.quality_cards(texts)
        if len(cards) < 2:
            # 标题认出来了但卡片还没淡入：等下一拍，别抢着点
            return False

        # 已经点过「确定」但页面还没切走（切页有淡出动画）：补点，别重新选一遍
        if time.monotonic() - getattr(self, "_cook_confirmed_at", 0.0) < 2.5:
            self.click_button("confirm", after_sleep=0.7, must=False)
            return True

        # 存证（未标注样本，供以后核对品质色判据）：同一页最多 10 秒存一次
        pick, ordered = self.quality_card_order(cards)
        now = time.monotonic()
        if now - getattr(self, "_cook_dumped_at", 0.0) > UNMARK_INTERVAL:
            self.dump_unmark(texts, "cook", cards, picked=pick["name"])
            self._cook_dumped_at = now

        detail = "｜".join(f"{c['name']}({c['quality']}/队友{c['mates']})"
                          for c in ordered)
        self.log_info(f"做饭：{detail} → 选「{pick['name']}」（{pick['quality']}）")

        box = pick["box"]
        # 点卡片**正文区**：标题条贴着卡片上沿，点边缘可能选不中
        cx = int(box.x + box.width / 2)
        cy = int(box.y + box.height / 2 + self.height * 0.06)
        self.click(cx, cy, after_sleep=0.5)
        self.info_set("做饭", pick["name"])
        self.sleep(0.3)          # 等选中动画走完，再点确定（否则确定会点空）
        if self.click_button("confirm", after_sleep=0.9, must=False):
            self._cook_confirmed_at = time.monotonic()
        else:
            self.log_warning("做饭：没认出「确定」按钮，下一拍重新判断这一页")
        return True
