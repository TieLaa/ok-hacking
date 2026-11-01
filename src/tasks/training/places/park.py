"""公园（散步 / 划船 / 野餐）：**地点模块**。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）：
每个地点有自己的模块，只管自己的判断。

公园的三个选项（`labels.PARK_OPTIONS`）：

    野餐 = 精力++   划船 = 属性+   散步 = 零用钱+

选哪个（用户 2026-10-30 / 2026-11-01）：

- **训练流**：周六 → 划船（属性+，不再满体力吃野餐）；
  周日 → 体力 < `周末野餐阈值` 就野餐补精力，否则划船。
  今天选过一次就记住（`park_choice`），不反复改。
- **其它流派**：用配置项 `公园选项` 指定的优先项（默认野餐）。

2026-10-26 踩过的坑（写在 docstring 里，避免又被改坏）：

    实机是三个**圆形图标**按钮，名字和效果都在图标下方（「划船 属性+」），
    页顶有「在公园干点什么好呢?」。旧判据 `labels.PARK_TITLE` 依赖
    「在公园」三个字，但实际文案是整句 —— 整句 OCR 时「在公园」不是独立框，
    match_text 的子串匹配靠"整段拼接"兜底才能命中，命中后返回的是**最长的框**
    （整句话），click_box 会点到句子中间，什么都点不到 → 卡死。

    所以：标题只当**判据**，实际点击永远走选项文字框。
"""

from src.tasks.ui import labels

# 地点名（地图上的按钮文字 / 状态里的键）
PLACE = "公园"

# 退出公园的兜底坐标（右上角返回）。
EXIT_COORDS = (0.945, 0.040)


class ParkPlace:
    """公园的判断与状态。**不点击**，只给判断。"""

    # ---------- 页面判据 ----------

    def park_page(self, texts):
        """这是不是公园内页？

        地图页/周末首页上有「出行」按钮，公园内页没有 —— 必须排除，
        否则地图页的对白「小澪奈总是吵着要去野餐…」会命中 `PARK_TITLE`
        里的「野餐」→ 把地图判成公园内页 → mark_visited("公园")
        → 反复点返回循环 9 拍，任务被判卡死停止（2026-10-27 事故）。
        """
        if self.match_text(texts, "出行") is not None:
            return False
        return self.match_text(texts, labels.PARK_TITLE) is not None

    # ---------- 今天选哪个（纯判断） ----------

    def park_order(self, texts, style, day=None):
        """今天点哪个公园选项？返回选项名的元组。

        `day` = 星期几（"六"/"日"/…）。读不到时按「非周日」处理（划船）。
        """
        if style != "训练流":
            pref = self.config.get("公园选项")
            return (pref,) + tuple(x for x in labels.PARK_OPTIONS if x != pref)

        st = self.day_state()
        day = day if day is not None else (st["date"][1] if st["date"] else None)
        if st.get("park_choice") is None:
            if day == "日":
                stamina = self.top_bar(texts).get("stamina")
                threshold = int(self.config.get("周末野餐阈值"))
                if stamina is not None and stamina < threshold:
                    st["park_choice"] = "野餐"
                    self.log_info(f"周日体力 {stamina} < {threshold} → 野餐补精力")
                else:
                    st["park_choice"] = "划船"
                    self.log_info(f"周日体力 {stamina} ≥ {threshold} → 划船（属性+）")
            else:
                st["park_choice"] = "划船"
                self.log_info("周六公园：划船（属性+，不再满体力吃野餐）")

        return (st["park_choice"],) + tuple(
            x for x in labels.PARK_OPTIONS if x != st["park_choice"])
