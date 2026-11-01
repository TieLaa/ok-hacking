"""vulture 白名单：这些「没人用」的东西**故意留着**，不要去删。

用法：`uvx vulture src ok_tasks tools --min-confidence 80 .vulture_whitelist.py`

为什么要有这个文件（2026-11-01）：
    第一次跑 vulture 报的 11 处里，5 处是**框架签名契约**——删了就炸，
    2 处是**失效开关**（不是死代码，是 bug，已经修成「真的静音」）。
    剩下 2 处真死参数已删（GirlsInteraction.move 的 down_btn、
    collect_ssr.Screen 的 ocr_every）。把这些写下来，下次再跑才不会重复判断一遍。

判断口径（重要）：
    「vulture 说没人用」≠「可以删」。先看它是不是在实现某个基类的签名
    （ok/device/interaction_methods/base.py），或者是不是调用方**传了但函数体没读**
    ——后者是 bug（开关失效），要修不要删。
"""

# --- ok 框架 interaction 接口的签名：task.py 会带这些 kwargs 调进来 ---
# ok/task/task.py:171        interaction.click(x, y, move_back=..., name=..., move=..., down_time=..., key=...)
# ok/device/interaction_methods/base.py:37  def click(self, x=-1, y=-1, move_back=False, ...)
move_back = None

# base.py:34  def swipe(self, from_x, from_y, to_x, to_y, duration, settle_time=0)
# 本项目的 GirlsInteraction.swipe 按毫秒解析 duration，settle_time 保留给基类契约。
settle_time = None
