"""训练业务的模块级常量。"""

import os

import cv2

# 项目根目录：constants.py 在 src/tasks/training/ 下，向上 4 层。
# 不要在各处用 dirname 数层数——重构目录时一定会数错（2026-10-26 踩过：
# dump_unknown 写到 src/logs/unknown，导致存证"消失"）。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

# 流派 → 周末去哪（按优先级，每天一个）
# 注意顺序：步行街扭蛋「消耗所有精力」，公园野餐「精力++」，
# 所以先扭蛋再野餐，相当于白拿 +48 属性；反过来就把回复浪费了。
STYLE_PLACES = {
    # 训练流：周末目标是把零用钱换成「有队友加成的黄色道具」，
    # 步行街 → 游戏厅，都没有可买的才回公园补精力（2026-10-26 用户口径）。
    # 注意 允许进步行街 / 允许进游戏厅 仍是显式开关，没开就退化成公园。
    "训练流": ["步行街", "游戏厅", "公园"],
    "扭蛋流": ["步行街", "公园"],
    "商店流": ["商店", "公园"],
    # 游戏厅只在「强化」里解锁了「可跳过已三星小游戏」之后才有意义，
    # 需要配置里显式打开「允许进游戏厅」；没三星的游戏仍需要真人打。
    "小游戏流": ["游戏厅", "公园"],
}

# 存证/调试帧的 JPEG 参数：q95 + 4:4:4 色度不降采样。
# 为什么不用 PNG：同一帧 PNG 约 2.4 MB，q95 约 0.48 MB，读图耗时与体积成正比。
# 为什么是 4:4:4：实测单像素最大色差从 94 降到 20（区域均值都在 1 以内），
# 项目有按像素取色复核品质/互动的需求，色度抽样会把颜色糊到相邻像素上。
# 旧版 OpenCV 没有 SAMPLING_FACTOR 常量时自动降级为默认 4:2:0。
JPEG_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, 95]
if hasattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR_444"):
    JPEG_PARAMS += [cv2.IMWRITE_JPEG_SAMPLING_FACTOR,
                    cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]
