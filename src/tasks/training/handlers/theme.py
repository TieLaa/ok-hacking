"""SSR 角色天赋触发的「请选择一个题材」三选一。

2026-11-02 用户实机截图（第 2 周 星期一，体力 100/130，零用钱 110，灵感 11，
角色 望月透 / 伊澄综合学园，状态「采风之后脑」）：

    标题「请选择一个题材」，三张竖卡横排，每张卡上沿一个题材标签：

        #末世  末世文的千层套路。
        #豪门  豪门的家长里短，吃瓜群众的最爱。
        #权谋  权御天下，谋士以身入局。

底部黄色「确定」，右上角「跳过」（快进图标）。

选哪张（用户 2026-11-02 三次修正后的**最终口径**）：

1. **优先红色**：哪张卡的标签是红字（粉/洋红都算），就选哪张
   —— 天赋触发的那张就是这个颜色（`#权谋` 那次是粉字）；
2. **都一样就选中间**：要么三张全白（`#悬疑/#治愈/#爽文` 那次），
   要么三张全红（用户第二次实报），都算「一样」→ 按 x 排序取中位那张。

注意判据**不是**「哪张跟别人不一样」：用户明确纠正过这一点。
所以**不能按位置选，也不能按黄框选**（三张卡顺序随机；黄框只是光标/默认选中，
第一次截图里黄框在中间 `#豪门`，要选的却是最右 `#权谋`）。

旧版没有这个 handler：OCR 认得全部文字，但没有 handler 认领 → 落 `unknown` 兜底，
在原地刷存证帧（`logs/debug/1102-220231_nochange3`，从 22:02 一直刷到 22:05，
任务等于停在这一页）。页面带「跳过」，所以**必须排在** `handle_dialog_continue` /
`handle_story_arrow` 之前，否则又被拿快进图标的点击吃掉。
"""

import time

from src.tasks.ui import labels


class ThemeHandlers:
    """角色天赋的题材三选一（mixin，方法通过 self 访问任务状态）。"""

    def handle_theme_select(self, texts):
        """「请选择一个题材」：有红字标签就选它；颜色都一样就选中间那张，再点「确定」。"""
        if self.exact_text(texts, labels.THEME_TITLE) is None:
            return False
        tags = [t for t in texts if labels.THEME_TAG_RE.match(t.name.strip())]
        if len(tags) < 2:
            return False

        # 已经点过「确定」但页面还没切走（切页有淡出动画）：补点，别重新判一遍颜色
        if time.monotonic() - getattr(self, "_theme_confirmed_at", 0.0) < 2.5:
            self.click_button("confirm", after_sleep=0.7, must=False)
            return True

        tags.sort(key=lambda t: t.x)
        scored = [(self.theme_tag_red_ratio(t), t) for t in tags]
        detail = "｜".join(f"{t.name.strip()} 红{r:.2f}" for r, t in scored)
        reds = [pair for pair in scored if pair[0] >= labels.THEME_TAG_RED_MIN]

        if len(reds) == 1:
            target = reds[0]
            self.log_info(f"题材：有唯一红字标签（{detail}）→ 选它")
        else:
            # 没有红字（三张全白），或者红的不止一张（三张全红/两张红）：
            # 用户口径「都一样就选中间」——按 x 排序取中位那张。
            target = scored[len(scored) // 2]
            why = "三张都红" if len(reds) == len(scored) else (
                "没有红字" if not reds else f"红字有 {len(reds)} 张、分不出主次")
            self.log_warning(f"题材标签{why}（{detail}）→ 按口径选中间那张")

        label = target[1].name.strip()
        cx = int(target[1].x + target[1].width / 2)
        # 点卡片**正文区**而不是标签本身：标签贴着卡片上沿，点在边缘可能选不中
        cy = int(target[1].y + target[1].height / 2 + self.height * 0.08)
        self.click(cx, cy, after_sleep=0.55)
        self.log_info(f"题材：选「{label}」")
        self.info_set("题材", label)
        self.sleep(0.3)          # 等选中动画走完，再点确定（否则确定会点空）
        self.click_button("confirm", after_sleep=0.9, must=False)
        self._theme_confirmed_at = time.monotonic()
        return True

    def handle_manuscript_submit(self, texts):
        """「WARD文档」汇报页：点右下角「交稿」。

        2026-11-02 用户截图（第 3 周）：标题「WARD文档」，左栏「收集到的idea」
        列着这几次用过的题材标签（#权谋 / #治愈 / #读心术），右侧正文是这次
        生成的小说，右下角黄色「交稿」，右上角「跳过」快进图标。

        判据 = `WARD文档` + `收集到的idea`。**不能只认标题**：页面底部（y≈0.93）
        还印着一个装饰用的「WARD文档」，只靠标题判的话会出现只有标题的中间态。
        页面带「跳过」，所以必须排在 `handle_dialog_continue` / `handle_story_arrow`
        之前（同「题材」页那条教训）。
        """
        for mark in labels.MANUSCRIPT_MARKS:
            if self.match_text(texts, mark) is None:
                return False
        # 已经点过「交稿」但结算还没出来：补点一次，别重复判页面
        if time.monotonic() - getattr(self, "_submit_confirmed_at", 0.0) < 2.5:
            self.click_button("submit", after_sleep=0.9, must=False)
            return True
        if not self.click_button("submit", after_sleep=0.9, must=False):
            # 按钮文字没认出来（正文把「交稿」吃了）：按实测相对坐标点右下角黄色按钮
            self.click(int(self.width * 0.787), int(self.height * 0.812),
                       after_sleep=0.9)
            self.log_warning("WARD文档：没认出「交稿」，按固定坐标点右下角按钮")
        else:
            self.log_info("WARD文档：交稿")
        self.info_set("状态", "交稿")
        self._submit_confirmed_at = time.monotonic()
        return True
