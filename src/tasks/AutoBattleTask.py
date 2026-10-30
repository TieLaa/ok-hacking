from ok import TriggerTask

from src.tasks.attempt import AttemptSupport


class AutoBattleTask(AttemptSupport, TriggerTask):
    """后台自动战斗骨架。

    这是 TriggerTask：执行器会按 trigger_interval 反复调用 run()，
    不需要用户点“开始”，只要在任务页启用即可。

    当前只搭好了循环和配置，识别部分留了占位。下一步：
    1. 启动游戏，用 Debug 模式截一张战斗画面；
    2. 在模板页框选“战斗标志”“技能按钮”等区域，生成 COCO 模板；
    3. 把下面 find_one(...) 里的名字换成真实模板名，并补上按键逻辑。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动战斗"
        self.description = "后台检测战斗画面，按配置自动释放技能（需先补充模板素材）"
        self.trigger_interval = 0.2  # 秒，两次检查之间的最小间隔
        self.default_config.update({
            "启用自动战斗": True,
            "技能按键": "e",
            "普通攻击按键": "left_click",
            "检查间隔(秒)": 0.2,
        })

    def reset_to_start(self):
        """骨架任务只读画面、不改游戏状态：重置就是确认能取帧。"""
        self.begin_attempt()
        return self.next_frame() is not None

    def verify_start_state(self):
        """没有可断言的业务基线，只要求采集链路可用。"""
        assert self.next_frame() is not None, "起始校验失败：拿不到画面"
        return True

    def run(self):
        self.begin_attempt()
        # 每次 run 都取一帧最新画面，避免用旧帧做判断
        frame = self.next_frame()
        if frame is None:
            return False

        # 模板还没做出来时直接跳过，避免任务一启用就报“找不到 feature”。
        if not self.feature_exists("battle_mark"):
            if not getattr(self, "_missing_mark_warned", False):
                self.log_info("模板 battle_mark 尚未创建，自动战斗暂不执行")
                self._missing_mark_warned = True
            return False

        # 1. 判断是否在战斗中。
        #    占位：在模板库里找一个叫 battle_mark 的图，阈值 0.8。
        #    battle_mark 需要你在 Debug 模式里框选生成。
        in_battle = self.find_one("battle_mark", frame=frame)
        if not in_battle:
            return False

        # 2. 战斗中的操作。
        #    真实逻辑通常是：检测技能是否亮起 -> 亮起就按；检测血量 -> 低血量吃药。
        #    这里只做一个最小示例：按一次技能键。
        skill_key = self.config.get("技能按键", "e")
        self.send_key(skill_key)

        # 3. 返回 True 表示“这一轮处理了”，执行器会重新开始触发轮询。
        return True
