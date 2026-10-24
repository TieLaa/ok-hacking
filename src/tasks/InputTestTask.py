import numpy as np

from ok import og
from src.tasks.MyBaseTask import MyBaseTask


class InputTestTask(MyBaseTask):
    """输入链路自检：点一下指定坐标，对比前后画面，判断点击有没有生效。

    用法：在任务页点「开始」，任务会
      1. 抓当前帧并保存；
      2. 按配置的坐标点击一次；
      3. 等待若干秒再抓一帧；
      4. 计算两帧差异并写入日志 / 界面。

    差异很小说明点击没进游戏（或点在了死区）；差异很大说明点中了。
    坐标以游戏客户区为基准，左上角是 (0, 0)，1920x1080。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "输入测试"
        self.description = "点击指定坐标并对比前后画面，验证当前交互方式能否操作游戏"
        self.default_config.update({
            "点击 X": 1635,
            "点击 Y": 910,
            "点击后等待(秒)": 1.5,
            "差异阈值(%)": 1.0,
        })

    def run(self):
        x = int(self.config.get("点击 X", 1635))
        y = int(self.config.get("点击 Y", 910))
        wait = float(self.config.get("点击后等待(秒)", 1.5))
        threshold = float(self.config.get("差异阈值(%)", 1.0))

        before = self.next_frame()
        if before is None:
            self.log_info("拿不到画面，检查截图方式/游戏窗口", notify=True)
            return False
        self.screenshot("input_test_before", before)

        interaction = "?"
        try:
            interaction = og.device_manager.interaction.__class__.__name__
        except Exception:
            pass
        self.log_info(f"使用 {interaction} 点击 ({x}, {y})")

        self.click(x, y)
        self.sleep(wait)

        after = self.next_frame()
        if after is None:
            self.log_info("点击后拿不到画面", notify=True)
            return False
        self.screenshot("input_test_after", after)

        if before.shape != after.shape:
            self.log_info(f"分辨率变化 {before.shape} -> {after.shape}，点击大概率生效", notify=True)
            self.info_set("输入测试", "生效（画面结构变化）")
            return True

        changed = (np.abs(before.astype(np.int16) - after.astype(np.int16)).max(axis=2) > 24).mean() * 100
        self.log_info(f"画面变化 {changed:.2f}%（阈值 {threshold}%）")
        if changed >= threshold:
            self.info_set("输入测试", f"生效 {changed:.1f}%")
            self.log_info("点击生效", notify=True)
            return True
        self.info_set("输入测试", f"疑似无效 {changed:.1f}%")
        self.log_info("点击可能没进游戏：差异过小", notify=True)
        return False
