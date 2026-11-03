"""佐久间雨乃的天赋「钓鱼」：钓鱼场三选一（转盘已并入 `wheel.py`）。

2026-10-30 用户实机截图（第1周 星期四，79/130，零用钱 120）：

1. 「请选择1个钓鱼场」——三张卡横排：
   家门口的小溪（0）/ 普通的钓鱼场（10）/ 专业的钓鱼场（20），
   卡上还有一句说明（水至清则无鱼 / 如果钓上这一条… / 风浪越大，鱼越贵），
   底部黄色「确定」。用户口径：**尽量选高价格的**。
2. 「钓鱼啦！」大转盘（左「手感一般」/ 右「运气超好」），底部黄色「转一转」
   —— 2026-11-03 起归 `handlers/wheel.py: handle_spin_wheel`（和做饭转盘共用）。

价格那三个数字（0/10/20）OCR **读不到**：实测整页只认出三张卡的标题、说明
和「确定」（logs/debug/1030-152606_nochange3/ocr.txt）。所以按位置取最右那张
——三张卡的价格就是左→右递增，最右 = 最贵（专业的钓鱼场 20）。

旧版没有这两个 handler，界面落到「没见过 → 点一下继续」，连点 3 次没变化后
任务卡死（logs/debug/1030-152608_nochange3）。
"""


class FishingHandlers:
    """钓鱼天赋的界面（mixin，方法通过 self 访问任务状态）。"""

    GROUND_NAMES = ("家门口的小溪", "普通的钓鱼场", "专业的钓鱼场")

    def handle_fishing_ground(self, texts):
        """钓鱼场三选一：选最贵的（最右），再点「确定」。"""
        cards = [t for t in texts if t.name.strip() in self.GROUND_NAMES]
        if self.exact_text(texts, ("请选择1个钓鱼场",)) is None and len(cards) < 2:
            return False

        if cards:
            cards.sort(key=lambda t: t.x)
            target = cards[-1]
            label = target.name.strip()
            # 价格数字偶尔能读到（2026-10-30 15:31 那帧读到「20」@(1152,584)）。
            # 能读到两个以上就按**价格**挑最贵的，读不到退回「最右」（价格左→右递增）。
            prices = []
            for t in texts:
                s = t.name.strip()
                if not s.isdigit():
                    continue
                cy = (t.y + t.height / 2) / self.height
                if 0.55 <= cy <= 0.76:
                    prices.append((int(s), t))
            if len(prices) >= 2:
                price, pbox = max(prices, key=lambda p: p[0])
                pcx = pbox.x + pbox.width / 2
                target = min(cards, key=lambda c: abs(
                    (c.x + c.width / 2) - pcx))
                label = f"{target.name.strip()}（{price} 币）"
            x = int(target.x + target.width / 2)
            # 点卡片中部而不是标题：标题贴着卡片上沿，点在边缘可能选不中
            y = int(target.y + target.height / 2 + self.height * 0.12)
        else:
            label = "最右卡（卡名没认出）"
            x, y = int(self.width * 0.81), int(self.height * 0.45)

        self.click(x, y, after_sleep=0.45)
        self.log_info(f"钓鱼场：选最贵的「{label}」"
                      "（价格读不到时按左→右递增取最右）")
        if not self.click_button("confirm", after_sleep=0.9, must=False):
            self.click(int(self.width * 0.5), int(self.height * 0.885),
                       after_sleep=0.9)
        self.log_info("钓鱼场：确定")
        return True

    # 「钓鱼啦！」那一页的转盘已并入 `handlers/wheel.py: handle_spin_wheel`
    # （2026-11-03 用户口径：「转一转」按钮的位置和文案是通用的，转盘内容随机、
    # 不影响决策）。这里只留钓鱼场三选一。
