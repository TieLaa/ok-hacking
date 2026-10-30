"""MCP 开发运行（ok-script >= 2.0.16）的通用适配层。

ok-script 的 MCP runner（``ok.core.mcp_runner``）对**提交的任务文件**有硬契约：

    class Task(BaseTask):            # 文件里必须有这个类，且实现下面三个方法
        def reset_to_start(self):    # 返回 False 算本次运行失败
        def verify_start_state(self):# 必须 assert 或返回 True，否则失败
        def run(self):               # 返回 False 算本次运行失败

runner 会先把 ``task.artifact_directory`` 指向本次运行目录，再依次调用
``reset_to_start()`` → ``verify_start_state()`` → ``run()``，最后 ``on_destroy()``。
GUI / CLI / 计划任务这三种入口直接调 ``run()``，不经过这些钩子。

所以任务类要同时支持两条路径：
- **MCP**：runner 已经重置并校验过，任务只负责产物目录与执行；
- **GUI/CLI**：任务自己在 run() 里重置、校验、建产物目录（失败只告警，不中断）。

本模块只提供「知道这次被谁拉起、产物往哪写」和安全的默认实现，
具体任务的起始状态判据放在各自的类里。
"""

import os
import time

import cv2

from src.tasks.training.constants import JPEG_PARAMS, PROJECT_ROOT

# GUI/CLI 运行的产物根目录（MCP 运行时用 runner 注入的目录，不落这里）。
ATTEMPT_ROOT = os.path.join(PROJECT_ROOT, "logs", "mcp", "attempts")


class AttemptSupport:
    """MCP / GUI 双路径运行支持，作为任务基类最左边的 mixin 使用。

    只依赖 ``self.log_info`` / ``self.frame``，不假设任务有 OCR 能力；
    需要界面判据的（大厅、比赛 HUD）在 BaseGirlsTask 里覆盖。
    """

    #: 单次运行最多往产物目录写几张图，防止长跑把磁盘塞满
    ATTEMPT_MAX_IMAGES = 20

    # ---------- 运行身份与产物目录 ----------

    @property
    def mcp_driven(self):
        """这次运行是不是 MCP runner 拉起的。

        runner 在调用任何钩子之前就注入 ``artifact_directory``；
        GUI/CLI 路径没有这个属性。
        """
        return bool(getattr(self, "artifact_directory", None))

    @property
    def attempt_dir(self):
        """本次运行的产物目录（绝对路径）。

        MCP：runner 注入的目录。
        GUI/CLI：``logs/mcp/attempts/<时间>_<任务名>/``，同一实例每次运行新建一个。
        存证统一走这里，MCP 的 ``get_run_status`` 才能把文件当成本次运行的证据。
        """
        injected = getattr(self, "artifact_directory", None)
        if injected:
            return str(injected)
        cached = getattr(self, "_attempt_dir", None)
        if cached:
            return cached
        name = getattr(self, "name", None) or type(self).__name__
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(name))
        folder = os.path.join(ATTEMPT_ROOT, f"{time.strftime('%m%d-%H%M%S')}_{safe}")
        os.makedirs(folder, exist_ok=True)
        self._attempt_dir = folder
        return folder

    def begin_attempt(self):
        """在 run() 最开头调用：定下本次产物目录并打一行日志，返回目录路径。

        TriggerTask 每拍都会调用；同一目录只初始化一次，避免把图片计数清零。
        """
        directory = self.attempt_dir
        if (getattr(self, "_attempt_started_at", None) is not None
                and getattr(self, "_attempt_dir", None) == directory):
            return directory
        self._attempt_images = 0
        self._attempt_started_at = time.time()
        self.log_info(
            f"本次运行产物：{directory}"
            f"（入口：{'MCP' if self.mcp_driven else 'GUI/CLI'}）")
        return directory

    # ---------- 存证 ----------

    def attempt_dump(self, tag, texts=None, frame=None):
        """把一帧 + OCR 文本写进本次运行目录，返回目录路径（失败返回 None）。

        与 ``dump_debug`` 的区别：dump_debug 固定写 ``logs/debug/<时间>_<tag>/``，
        那是给人翻的；MCP 运行要求产物落在 ``artifact_directory`` 里。
        图仍用 JPEG q95 + 4:4:4（见 constants.JPEG_PARAMS）。
        """
        try:
            if int(getattr(self, "_attempt_images", 0) or 0) >= self.ATTEMPT_MAX_IMAGES:
                self.log_debug(f"产物图已达上限 {self.ATTEMPT_MAX_IMAGES}，跳过 {tag}")
                return None
            folder = os.path.join(self.attempt_dir, "frames")
            os.makedirs(folder, exist_ok=True)
            stamp = time.strftime("%H%M%S")
            base = os.path.join(folder, f"{stamp}_{tag}")
            image = frame if frame is not None else getattr(self, "frame", None)
            if image is not None and getattr(image, "any", lambda: False)():
                cv2.imwrite(base + ".jpg", image, JPEG_PARAMS)
            if texts is None:
                try:
                    texts = self._ocr_region()
                except Exception:                # noqa: BLE001
                    texts = []
            with open(base + ".txt", "w", encoding="utf-8") as f:
                f.write("\n".join(
                    f"{t.name}\t({t.x},{t.y},{t.width},{t.height})" for t in texts or []))
            self._attempt_images = int(getattr(self, "_attempt_images", 0) or 0) + 1
            self.log_info(f"存证 {os.path.relpath(base, PROJECT_ROOT)}")
            return folder
        except Exception as e:                   # noqa: BLE001
            self.log_debug(f"attempt_dump 失败: {type(e).__name__}: {e}")
            return None

    # ---------- MCP 契约默认实现 ----------

    def reset_to_start(self):
        """默认：不改游戏状态的任务（纯读取/演示类）用这个。

        会改游戏状态的任务必须覆盖它，回到可复现的基线。
        """
        self.begin_attempt()
        return True

    def verify_start_state(self):
        """默认：没有可断言的基线，直接放行。覆盖时请 assert，别 return False。"""
        assert self.next_frame() is not None, "起始校验失败：拿不到画面"
        return True

    def on_destroy(self):
        """清掉本次运行的状态；MCP 和 GUI 都会在任务结束时调用。

        用 delattr 而不是置 None：置 None 会让下一次 `getattr(..., 0)` 拿到 None，
        attempt_dump 里的比较直接抛 TypeError（异常被吞掉，表现是「存证没生成」，
        2026-10-30 实跑踩过）。
        """
        for attr in ("_attempt_images", "_attempt_started_at"):
            if hasattr(self, attr):
                try:
                    delattr(self, attr)
                except AttributeError:
                    pass
        super_method = getattr(super(), "on_destroy", None)
        if callable(super_method):
            super_method()
