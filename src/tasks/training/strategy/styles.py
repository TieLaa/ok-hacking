"""流派策略：最终形状（接口先行，**尚未接入**）。

一段话说明白这个文件是干什么的：

    游戏里每个队长的「流派」不同，导致「周末去哪、商店要不要少买、公园选什么」
    的答案不同。这个文件就是这些答案的清单 ——
    上半部分是给人看的（每天每个流派干什么），
    下半部分是给程序看的（那些答案写成一类叫 Style 的东西）。

**怎么读这个文件**：

    - `Style` 的一个子类 = 一个流派。类属性就是答案（值），方法就是判断。
    - 值：现在就能查，`style_spec("扭蛋流").weekend_main` → "步行街"。
    - 方法：**还没接**。现在调用会报错，因为实现还在 places/*.py 里。
      「接入」= 把调用点从「自己写 if 流派 == ...」改成「问这个类」。
      一条都没接之前，程序跑起来完全不会用这里的方法。
    - 「待迁 / 不改 / 待定」见业务地图里的标记说明。

三部分：按天业务地图（注释）→ 值（类属性，可用）→ 判断（方法，未接）。

清单最后核对：2026-11-17（对齐 `d350d5e` / `3cf2e0c` 之后的代码）。
"""

# ============================================================================
# 一、术语（游戏里的说法，先看这个）
# ============================================================================
#
#   主地点    周末花钱去的地方（步行街=抽卡 / 游戏厅=小游戏）。
#             训练流 / 商店流没有：它们周末只打工或回精力，不花钱。
#             注意和「探卡池」区别：探卡池是所有流派出行日都先去步行街看一眼；
#             主地点是进去正常消费。
#   行程      今天按顺序去哪几个地方，前一个走完才轮到下一个
#   收工      这件事做完今天就不再出门（买到扭蛋就不再去餐厅打工）
#   缺口      精力上限 − 当前精力；差多少才补得回来
#   留钱      少买东西，把零用钱留到周末或下一次出行
#   探卡池    出行日第一步先去步行街，看有没有两张彩色目标卡
#   科目页    点「训练」后选练哪个属性（投射/弹跳/防守/运控/体能/意识）的那一页
#
# ============================================================================
# 二、一天里的顺序（所有流派共用）
# ============================================================================
#
#   开局 / 换队长   定流派：配置了队长 → 查表；没配置 → 点日历认队长（flow.py）
#   每天开始        读日期 / 精力 / 零用钱，每帧只读一次（daycycle.top_bar）
#   训练            一律先点「训练」进科目页；失败率超限才退出来休息
#                   （daycycle.training_day_action）
#   进店            有「新品上市」标签就进；第 7 周周日强制进
#                   （daycycle.late_shop_step）
#   出行            有「出行」按钮就去（daycycle.outing_offer）
#   出行中          地图决定去哪 → 地点内决定做什么（places/map.py → places/*.py）
#
# ============================================================================
# 三、训练日（周一~周五；有没有出行看按钮，不看日期）
# ============================================================================
#
#   事项          训练流        商店流        扭蛋流            小游戏流
#   练 / 休        先进科目页     先进科目页     先进科目页         先进科目页
#   周四低精力     ——            ——            精力 < 60 直接休   精力 < 60 直接休
#   进店           有标签就进     有标签就进     有标签就进         有标签就进
#   店内买什么     同顺序         同顺序         同顺序             同顺序
#   店内留钱       不留           不留           前 6 周只拿必拿    前 6 周只拿必拿
#   有出行按钮     见第四节       见第四节       见第四节           见第四节
#
#   已迁  前 6 周只拿必拿   → places/shop.py: save_money_for_weekend（读 saves_money）
#   待定  周四低精力阈值   → 现读 params.rest.thursday_threshold_by_style
#
# ============================================================================
# 四、周末（周六、周日）
# ============================================================================
#
#   事项          训练流                  商店流                扭蛋流              小游戏流
#   今天去哪      见下方「训练流细则」     餐厅                  步行街 → 餐厅        游戏厅 → 餐厅
#   钱不够时      不看钱                  不看钱                <360 只去餐厅打工    同扭蛋流
#   主地点成交后  ——                      ——                    买到即收工          靠「今天去过」过滤
#   公园选什么    缺口 ≥70 野餐           配置默认（野餐）      同商店流             同商店流
#                 缺口 <70 划船           队友多 2 → 划船       队友多 2 → 划船      队友多 2 → 划船
#   场所开关关了  不涉及                  不涉及                步行街关 → 餐厅      游戏厅关 → 餐厅
#
#   训练流细则（缺口 = 精力上限 − 当前精力）：
#     缺口 < 30              → 餐厅 → 公园（打不了工还有公园兜底）
#     缺口 ≥ 30 · 周六       → 餐厅 → 公园（单卡队友 ≥3 人才真打工，分散就退出回公园）
#     缺口 ≥ 30 · 周日       → 公园（精力花光，下周训练日会少一天）
#     第 7 周周六 · 钱 > 120 → 步行街 → 餐厅（刷最后一次彩卡，没买成才打工）
#     第 7 周周六 · 钱 ≤ 120 → 餐厅
#     缺口读不到             → 餐厅 → 公园
#
#   注意：训练流那一行的「今天去哪」不是读表，是算法（按缺口 + 星期 + 周数算）。
#   值表里给训练流的 weekend_plan = ("餐厅",) 只是兜底值，正常不会用到。
#
#   已迁  今天去哪 / 行程 / 收工  → places/map.py: today_places（读值表）
#   已迁  公园队友优先            → places/park.py: park_default_pick（读 teammate_first）
#   已迁  步行街先看钱            → places/street.py（读 cash_first）
#   不改  训练流按缺口补训        → places/map.py: training_weekend_places（训练流独占）
#   不改  训练流公园按缺口        → places/park.py: park_pick（训练流独占）
#   不改  训练流周六餐厅「看一眼」 → places/restaurant.py: restaurant_preference 前的守卫
#                                 （单卡 <3 人 → 退出餐厅，交回地图）
#   不改  钱不够只去餐厅          → places/map.py: prefer_venue
#
# ============================================================================
# 五、叠加在任何天上的规则（都要读当场数据，接入 ctx 前留在原函数）
# ============================================================================
#
#   规则          谁            内容                                          位置
#   探卡池        所有流派      出行日第一步先去步行街：
#                               · 两张彩色目标卡（笔记继承 / 球员位置）照买；
#                               · 其它彩色卡也用闲钱买（按下面的「留钱」规则）；
#                               · 买到一张目标卡后，这趟转成正常买卡模式。
#                                                                           map.gacha_probe
#                                                                           street.gacha_candidates
#   急招          所有流派      餐厅有急招且主计划不是餐厅 → 先插一趟           map.event_detour
#                               （第 7 周周六不插）
#   步行街打折    只有扭蛋流    折后够、原价不够 → 先绕去步行街                 map.map_context
#   第 3 周周末   所有流派      体力低于海边目标 → 把打工换成公园               map.vacation_eve_guard
#   最后一周      所有流派      整周不买训练类；最后一天只买属性（蓝白也买）    shop.pick_shop_item
#   留钱          所有流派      最后一天之前买完要剩 120（留给步行街两张卡）；
#                              第 7 周周六起不再留（商店和探卡池都放开）      shop.pick_shop_item
#                                                                           street.gacha_candidates
#   第 7 周周日   所有流派      不能出行，只进商店清零花钱                      map.today_places + daycycle
#   比赛日        所有流派      按配置跳过或加速                               handlers/match.py
#   专属卡归属    见词表        训练流/小游戏流/扭蛋流各自吃哪些卡              attributes.card_style_target
#

class Style:
    """一个流派：默认值 + 默认行为，子类只写差异。

    类属性 = 值（现在就能查）；方法 = 判断（接入 ctx 前一律抛错）。
    字段注释写的是「这个字段回答哪个业务问题」，不是类型说明。
    """

    # ---------------- 值（已可用） ----------------
    name = ""                     # 我是谁（和 docs/roster 里的 style_tag 一致）
    weekend_main = None           # 周末花钱去哪个地点？None = 周末不专门消费
    weekend_plan = ("餐厅",)      # 没有主地点时，今天按顺序去哪？
    ends_day_after_venue = False  # 主地点买到/玩完，今天还去餐厅打工吗？
    saves_money = False           # 前 6 周商店要不要少买、把钱留给周末？
    cash_first = False            # 步行街：先看钱够不够，还是先看卡好不好？
    teammate_first = False        # 公园：队友多 2 个就改划船吗？
    thursday_rest = False         # 周四精力低要不要直接休息？（阈值在 params）

    # ---------------- 判断（尚未接入，调用即报错） ----------------
    #
    # 每个方法的实现都已在别处，接入时只做「搬 + 改签名」：
    #   docstring 末尾写上现有位置，方便照着改。

    def weekend_places(self, ctx):
        """今天去哪（有序）。基类默认 = 值表；训练流按精力缺口 + 星期覆盖。

        现有实现：places/map.py: today_places / training_weekend_places
        """
        raise NotImplementedError("weekend_places 尚未接入；见 places/map.py")

    def prefer_venue(self, ctx):
        """今天先去哪个。主地点流看零用钱门槛，训练流不走这条。

        现有实现：places/map.py: prefer_venue
        """
        raise NotImplementedError("prefer_venue 尚未接入；见 places/map.py")

    def pick_park(self, ctx):
        """公园选散步 / 划船 / 野餐。训练流按缺口，其它流派队友优先。

        现有实现：places/park.py: park_pick
        """
        raise NotImplementedError("pick_park 尚未接入；见 places/park.py")

    def pick_work_card(self, ctx):
        """餐厅选哪张卡打工。基类 = 按工资 / 队友排序；训练流周六「看一眼」趟
        要求单卡队友 ≥3 人，两张卡分散（2+2）就退出餐厅交回地图。

        现有实现：places/restaurant.py: restaurant_preference 前的守卫
        """
        raise NotImplementedError("pick_work_card 尚未接入；见 places/restaurant.py")

    def wants_shop(self, ctx):
        """今天要不要进店。基类 = 有「新品上市」就进（所有流派相同）。

        现有实现：daycycle.py: late_shop_step
        """
        raise NotImplementedError("wants_shop 尚未接入；见 daycycle.py")

    def pick_shop_item(self, ctx):
        """店里买哪件。基类 = 全局同一条顺序，流派只影响「留多少钱」。

        现有实现：places/shop.py: pick_shop_item
        """
        raise NotImplementedError("pick_shop_item 尚未接入；见 places/shop.py")

    def pick_street_card(self, ctx):
        """步行街挑哪张卡。基类 = 专属卡按流派过滤 + 本流派卡优先 + 探卡池模式。

        现有实现：places/street.py: gacha_candidates / handle_street
        """
        raise NotImplementedError("pick_street_card 尚未接入；见 places/street.py")

    def should_rest(self, ctx):
        """练还是休。默认先进科目页；扭蛋流 / 小游戏流周四有 60 门槛。

        现有实现：daycycle.py: training_day_action
        """
        raise NotImplementedError("should_rest 尚未接入；见 daycycle.py")

    def event_detour(self, ctx):
        """急招 / 打折要不要改道。基类 = 只看急招；扭蛋流额外看步行街打折。

        现有实现：places/map.py: event_detour / map_context
        """
        raise NotImplementedError("event_detour 尚未接入；见 places/map.py")


class TrainingStyle(Style):
    """训练流（游戏里练属性为主那类队长）。

    工作日
        · 训练日先进科目页；失败率超限才退出来休息
        · 周四没有特殊规则
        · 商店有「新品上市」就进；买什么和别的流派同一条顺序
    周末（按精力缺口 = 上限 − 当前）
        · 缺口 < 30              → 餐厅 → 公园（打不了工还有公园兜底）
        · 缺口 ≥ 30 · 周六       → 餐厅 → 公园（单卡队友 ≥3 人才真打工，分散就回公园）
        · 缺口 ≥ 30 · 周日       → 公园（精力花光，下周训练日会少一天）
        · 第 7 周周六 · 钱 > 120 → 步行街 → 餐厅（刷最后一次彩卡）
        · 第 7 周周六 · 钱 ≤ 120 → 餐厅
    公园
        · 缺口 ≥ 70 野餐，< 70 划船；队友不参与（缺口优先）
    钱
        · 不需要留：不进步行街也不进游戏厅，卡对训练流以外没用
    """

    name = "训练流"


class ShopStyle(Style):
    """商店流（钱主要花在商店那类队长）。

    周末固定去餐厅打工，商店是唯一消费口 —— 所以步行街也先看钱（cash_first），
    公园按「队友多就划船」拿属性（teammate_first），不刻意省精力。
    """

    name = "商店流"
    cash_first = True
    teammate_first = True


class GachaStyle(Style):
    """扭蛋流（周末去步行街抽卡那类队长）。

    工作日
        · 周四精力 < 60 直接休息（保证周五还能进出行地图）
        · 商店前 6 周只拿免费红包，钱留给周末
    周末
        · 去步行街；钱 < 360 就先只去餐厅打工攒钱，攒够再去
        · 抽到卡当天收工，不再去餐厅 —— 钱已经花掉了
    公园
        · 队友比野餐多 2 个 → 划船；否则配置默认（野餐）
    """

    name = "扭蛋流"
    weekend_main = "步行街"
    weekend_plan = ("步行街", "餐厅")
    ends_day_after_venue = True
    saves_money = True
    cash_first = True
    teammate_first = True


class MiniGameStyle(Style):
    """小游戏流（周末去游戏厅玩小游戏那类队长）。

    和扭蛋流几乎一样：周末去游戏厅、前 6 周留钱、钱不够先打工、周四低精力休息。
    唯一不同：玩完小游戏游戏会自己结束，「今天去过游戏厅」本身就是标记，
    不需要额外的「收工」开关（ends_day_after_venue = False）。
    """

    name = "小游戏流"
    weekend_main = "游戏厅"
    weekend_plan = ("游戏厅", "餐厅")
    saves_money = True
    cash_first = True
    teammate_first = True


STYLES = {s.name: s() for s in
          (TrainingStyle, ShopStyle, GachaStyle, MiniGameStyle)}

# 流派没定 / 认不出名字时的兜底：`cash_first` / `teammate_first` 按
# **非训练流**处理（与收口前的 `style != "训练流"` 等价）；`saves_money` 更保守 ——
# 认不出流派时宁可少花钱（同 `shop.save_money_for_weekend`）。
class UnknownStyle(Style):
    """兜底流派：名字为空，行为按「非训练流」走。"""

    name = ""
    cash_first = True
    teammate_first = True
    saves_money = True


UNKNOWN = UnknownStyle()


def style_spec(name):
    """流派名 → 该流派的实例。认不出返回 UNKNOWN（不抛 KeyError）。"""
    return STYLES.get(name or "") or UNKNOWN


# ============================================================================
# 六、接入方法时需要什么（ctx）
# ============================================================================
#
# 上面 9 个方法现在只能拿到 `ctx`，不能拿整个任务对象 —— 否则等于把 if 搬进类里。
# ctx 是**每拍重新读一遍**的当场数据，不跨天保存：
#
#   week / day     第几周、星期几
#   money          零用钱
#   stamina        当前精力；stamina_max 精力上限
#   config         用户配置（公园选项、允许进步行街/游戏厅、周末划船缺口）
#   fresh_badge    商店上方有没有「新品上市」
#   urgent         餐厅有没有「急招！」
#   discount       步行街有没有「打折！」
#   visited        今天去过哪些地点
#   gacha_spent    今天在步行街买到卡了没有
#   mate_counts    餐厅 / 公园各选项的队友数
#
# 这些数据现在散在任务对象和各模块里，打包成 ctx 是一次独立改动，
# 没做之前，9 个方法只保留签名，不实现。
#
# ============================================================================
# 七、迁移顺序（一次只走一条，改完把业务地图里的「待迁」划掉）
# ============================================================================
#
#   1~5 已完成（2026-11-17）：商店留钱、周末行程、公园队友、步行街钱优先
#   四处调用点已改成读值表；constants.py 的 WEEKEND_MAIN_PLACE / WEEKEND_PLAN
#   已删除。
#
#   剩下的是 ctx + 9 个方法；它们不动，程序行为就一个字都没变。
