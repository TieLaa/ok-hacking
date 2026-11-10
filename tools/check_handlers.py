"""离线回归：用**已有截图**验证所有 handler 的判据，不启动游戏。

为什么要有这个：
    之前每改一次判据都要「重启 ok-script → 等游戏加载 → 跑 60 秒 → 看日志」，
    一轮 5~10 分钟，而且只有真撞上那个界面才知道错。
    现在改成：截图 → OCR → 对全部 handler 干跑一遍 → 3 秒出结果。

用法：
    python tools/check_handlers.py                  # 扫 docs/shots/*.png，比对 expected.json
    python tools/check_handlers.py --cache          # 复用 OCR 缓存（快 5 倍）
    python tools/check_handlers.py --all-hits       # 额外列出「同时命中」的 handler（查抢占）
    python tools/check_handlers.py 图.png            # 只测指定图片
    python tools/check_handlers.py --update-expected # 登记新图的期望值（人工确认后再提交）

退出码：有「未命中」或「和 expected.json 不一致」时返回 1，可以直接进 pre-commit / CI。
"""

import ast
import glob
import json
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ok.feature.Box import Box  # noqa: E402

from src.tasks.BaseGirlsTask import BaseGirlsTask  # noqa: E402
from src.tasks.TrainingTask import TrainingTask  # noqa: E402

CACHE_DIR = os.path.join(ROOT, "docs", "shots", ".ocr_cache")

# expected.json 里的这个值表示「该图不该被任何 handler 认领」（防误判样例）
MISS_MARK = "-"

from tools._console import utf8_stdout, write_report  # noqa: E402
from tools import _ocr  # noqa: E402


def run_ocr(frame):
    """拿 OCR 引擎（进程内单例），实现见 `tools/_ocr.py`。"""
    return _ocr.engine()


def ocr_to_boxes(ocr, frame):
    """一帧 → `[Box, …]`（外接矩形 + confidence），解析只在 `tools/_ocr.py` 一处。"""
    return _ocr.boxes(frame, ocr)


class _FakeMethod:
    def __init__(self, width, height):
        self.width = width
        self.height = height


class _FakeExecutor:
    """`frame` / `width` / `height` 都是 BaseTask 的 property，底层读 self._executor，这里给个假的。"""

    def __init__(self, frame):
        self.frame = frame
        self.method = _FakeMethod(frame.shape[1], frame.shape[0])


class _NullEvidence:
    """回归跑**不落盘**：存证调用全部是空操作（2026-11-10 落盘归口后统一在这里挡）。

    以前只挡了 `dump_unmark`，结果 `dump_frame`（技能/炒股/科目帧）绕过去，
    每跑一次 `check_handlers` 就往 `logs/debug` 塞几张图。
    """

    root = ""
    round_dir = ""
    round_id = ""

    @staticmethod
    def _noop(*args, **kwargs):
        return None

    def begin_run(self, *args, **kwargs):
        return ""

    def begin_round(self, *args, **kwargs):
        return ""

    def end_round(self, *args, **kwargs):
        return None

    def __getattr__(self, _name):
        return self._noop


class DryRunTask(TrainingTask):
    """只判断、不动作的 TrainingTask。

    覆盖掉所有会产生副作用的方法（点击 / 等待 / OCR 请求），
    这样可以把真实 handler 当纯函数跑。
    """

    def __init__(self, frame, texts):
        self._executor = _FakeExecutor(frame)
        self._texts = texts
        # 配置用**真实的 default_config**（config_spec 生成）——
        # 和生产环境一样（ok.util.config 会用 default_config 补全所有键）。
        #
        # 2026-11-01 之前这里是 `{"无队长不接开局": False}` —— 只留一个键，
        # 靠「每个调用处都写 `config.get(key, 默认值)`」来兜底。
        # 去掉调用处的死默认值之后（默认值只留在 config_spec 一处），
        # 缺键就会拿到 **None**（falsy）→ 「允许进步行街」被当成 False，
        # 25 条测试挂掉。**夹具必须和生产一样**，否则测的就不是真实行为。
        #
        # 例外：`无队长不接开局` 关掉，否则空队长会触发「只跑已有局」的闸门，
        # 开局页全部不再执行，回归就测不到启程/选人/笔记/器材。
        from src.tasks.training import config_spec
        self.config = dict(config_spec.build_default_config())
        self.config["无队长不接开局"] = False
        self.actions = []
        self.logs = []
        self.name = "dry-run"
        # 与 BaseTask 保持一致：handler 会往 info 写监控页数据
        self.info = {}
        # handler 里会用到的一些状态
        self._steps = 1
        self._skip_tries = 0
        self._last_state = None
        self._picked_subject = False
        self._picked_index = 0
        self._match_flags = {"manual_fixed": False, "fast_clicked": False, "last_fast_at": 0.0}
        self._weekend = {"date": None, "shop_checked": False, "bought": False,
                         "park_choice": None}
        self._shop_day = None
        self._shop_spend_all = False
        self._shop_seen = False
        self._speed_week = None
        self._speed_locked = False
        self._subject_cache = {"date": None, "best": None}
        self._force_rest = False
        # 最近一次点击的墙钟时间（点击流水 / 复盘用；卡住判据已改成数点击）
        self._last_click_at = 0.0
        # 卡住判据的计数器（TrainingTask.__init__ 里也有，夹具要和生产一致）：
        # 同一天「点了多少下 / 接管过几次」，日期一更新（`day_counter_step`）归零
        self._day_key = None
        self._day_clicks = 0
        self._day_fixes = 0
        self._sig_changed_at = 0.0
        # 本局收账（「训练评分」弹层读分）：夹具也要有默认值，否则 handler 直接 AttributeError
        self._run_score = None
        self._round_done = False
        self._round_score_seen = False
        self._score_click_at = 0.0
        self._match_intro_at = 0.0
        # 流派识别（2026-10-29）：None = 还不知道，等着开日历认队长
        self._style = None
        self._style_name = None
        self._style_probe_tries = 0
        self._style_probe_at = 0.0

    # ---------- 坐标换算 ----------
    def width_of_screen(self, ratio):
        return self.width * ratio

    def height_of_screen(self, ratio):
        return self.height * ratio

    def box_of_screen(self, x1, y1, x2, y2):
        return Box(x=int(self.width * x1), y=int(self.height * y1),
                   width=int(self.width * (x2 - x1)), height=int(self.height * (y2 - y1)))

    # ---------- OCR ----------
    def _ocr_region(self, region_name=None, box=None):
        return self._texts

    # ---------- 动作（只记录） ----------
    def click(self, x=-1, y=-1, **kwargs):
        self.actions.append(("click", int(x), int(y)))

    def click_box(self, box, **kwargs):
        cx = int(box.x + box.width / 2)
        cy = int(box.y + box.height / 2)
        self.actions.append(("click_box", box.name, cx, cy))

    def click_text(self, names, region_name=None, box=None, must=True, **kwargs):
        target = self.find_text(names, region_name, box)
        if target is None:
            return False
        self.click_box(target)
        return True

    def click_region(self, region_name, **kwargs):
        self.actions.append(("click_region", region_name))
        return True

    def click_and_expect(self, x, y, expect, **kwargs):
        self.actions.append(("click_and_expect", int(x), int(y), str(expect)))
        return True

    def click_main_action(self, **kwargs):
        return self.click_region("main_action")

    def back(self, **kwargs):
        self.actions.append(("back",))

    def sleep(self, *args, **kwargs):
        pass

    def info_set(self, *args, **kwargs):
        pass

    def dump_unmark(self, *args, **kwargs):
        """回归跑不落盘：否则每跑一次 check_handlers 就往 logs/ 里塞存储证据。"""
        return None

    #: 存证口整套换成空操作（新结构下 dump_frame / snap_event 也走它）
    evidence = _NullEvidence()

    # ---------- 日志 ----------
    def log_info(self, msg, **kwargs):
        self.logs.append(str(msg))

    log_debug = log_info
    log_warning = log_info
    log_error = log_info

    # ---------- 复用真实实现 ----------
    def find_text(self, names, region_name=None, box=None):
        return BaseGirlsTask.find_text(self, names, region_name, box)


def load_boxes(path, ocr_holder, use_cache=True):
    if use_cache:
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache = os.path.join(CACHE_DIR, os.path.basename(path) + ".json")
        if os.path.exists(cache):
            with open(cache, "r", encoding="utf-8") as f:
                return [Box(**d) for d in json.load(f)]
    frame = cv2.imread(path)
    if frame is None:
        return None
    if ocr_holder.get("ocr") is None:
        ocr_holder["ocr"] = run_ocr(frame)
    boxes = ocr_to_boxes(ocr_holder["ocr"], frame)
    if use_cache:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump([{"x": b.x, "y": b.y, "width": b.width, "height": b.height,
                        "confidence": b.confidence, "name": b.name} for b in boxes], f,
                      ensure_ascii=False)
    return boxes


def probe(path, ocr_holder, use_cache=True, verbose=False):
    """返回 (path, [(handler_name, actions), ...])，按优先级列出**所有**命中。

    每个 handler 用独立的 DryRunTask，避免前面的命中污染后面的判据。
    """
    frame = cv2.imread(path)
    if frame is None:
        return path, []
    boxes = load_boxes(path, ocr_holder, use_cache)
    if boxes is None:
        return path, []
    hits = []
    for handler in TrainingTask.HANDLERS:
        task = DryRunTask(frame, boxes)
        try:
            if handler(task, boxes):
                hits.append((handler.__name__, task.actions))
        except Exception as e:
            if verbose:
                print(f"    ! {handler.__name__} 抛异常: {type(e).__name__}: {e}")
    return path, hits


EXPECTED_PATH = os.path.join(ROOT, "docs", "shots", "expected.json")


def load_expected():
    """期望表 {截图文件名: 应该命中的 handler}。

    新截图先跑 `--update-expected` 落盘，再人工确认一次结果是否合理。
    """
    if not os.path.exists(EXPECTED_PATH):
        return {}
    with open(EXPECTED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_expected(exp):
    with open(EXPECTED_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dict(sorted(exp.items())), f, ensure_ascii=False, indent=2)
        f.write("\n")


def describe(actions):
    if not actions:
        return "-"
    return " ".join(str(x) for x in actions[0])[:46]


def check_architecture():
    """结构断言（ast，不 import 代码）：挡住「用 handler 名字做判断分支」的回退。

    背景：`flow._handle_any` 曾经用 `if name != "handle_prepare": self._skip_tries = 0`
    表达「handler 之间有依赖」，于是每加一个 handler 都要回去改 flow。
    2026-11-01 改成 `handlers/__init__.py: LEAVE_RESETS` 声明表 + 自动清零。

    规则（精确到"判断分支"，不是一刀切）：
      ✗ 禁止：比较/`in` 判断里出现 handler 名字字面量（`if name != "handle_x"`、`if state in ("handle_x",)`）
      ✓ 允许：模块级**声明表**（集合/字典里列 handler 名，如 `SNAP_HANDLERS`、`LEAVE_RESETS`）
    后者是数据，前者是把依赖写进控制流 —— 只拦后者。

    为什么用 ast 不用 grep：`from . import match`、别名导入、注释/字符串里的字样，
    grep 会漏或误报；ast 只看真正的比较表达式。

    返回问题列表（空 = 通过）。顺带打印 `self._xxx = …` 赋值数当**状态散装度基线**（只统计不拦截）。
    """
    handler_names = set()
    handler_dir = os.path.join(ROOT, "src", "tasks", "training", "handlers")
    for f in os.listdir(handler_dir):
        if not f.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(handler_dir, f), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("handle_"):
                handler_names.add(node.name)

    problems = []
    state_assignments = 0
    for dp, _, files in os.walk(os.path.join(ROOT, "src")):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(dp, f)
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)

            # 基线：`self._xxx = …` 赋值次数（跨拍状态散装度）
            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                                and t.value.id == "self" and t.attr.startswith("_"):
                            state_assignments += 1

            # 违规：判断分支里的 handler 名字字面量
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                operands = [node.left] + list(node.comparators)
                for sub in operands:
                    for c in ast.walk(sub):
                        if isinstance(c, ast.Constant) and isinstance(c.value, str) \
                                and c.value in handler_names:
                            problems.append(
                                f"{rel}:{c.lineno} 判断分支里出现 handler 名字「{c.value}」——"
                                f"依赖要写进 handlers/__init__.py 的声明表，不要写进控制流")

    print("-" * 96)
    print(f"结构断言：判断分支里的 handler 名字 {len(problems)} 处；"
          f"self._xxx 赋值 {state_assignments} 处（状态散装度基线，只统计）")
    for p in problems:
        print("  ✗ " + p)
    return problems


# ---------- 全局日期单一入口（2026-11-01 架构）----------
# 用户 2026-11-01 定的架构：**全局日期只有一处读**。
# 「一天」的标识是顶栏的「第N周 星期X」；游戏里交互就过天，所以
# 日期变了就是新的一天 —— 不需要各模块自己维护「今天过没过」。
#
# 为什么必须收口（真实 bug）：`top_bar()` 会被 20 多个地方调用，
# 同一帧里各读一次可能读到不同的值（OCR 抖动）。`day_state()` 原来
# 按「日期键变了就重建整个状态」→ 顶栏抖一帧 → visited/gacha_spent
# 全被清空 → 当天「已经买过扭蛋」的事实丢了。
#
# 规则：
#   ✗ 禁止：handlers/ 下的模块直接调 `self.top_bar()` 取**日期**
#     （专注/精力等其它字段仍可取 —— 那是另一回事，见下）
#   ✓ 允许：daycycle.py 自己读（它就是那个公共入口）
#   ✓ 允许：parsers.py（顶层解析层，读顶栏是它的本职）
def check_date_entry():
    """全局日期只能从 `daycycle.current_day()` 拿。

    返回问题列表（空 = 通过）。
    """
    problems = []
    handlers_dir = os.path.join(ROOT, "src", "tasks", "training", "handlers")
    for f in sorted(os.listdir(handlers_dir)):
        if not f.endswith(".py"):
            continue
        rel = "src/tasks/training/handlers/" + f
        path = os.path.join(handlers_dir, f)
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "top_bar"):
                continue
            # 用了 .get("week") / .get("day") 才算「取日期」
            src = ast.unparse(node)
            if '"week"' in src or '"day"' in src:
                problems.append(
                    f"{rel}:{node.lineno} 直接读 top_bar() 取日期——"
                    f"改用 daycycle 的 current_day() / day_state()")
    print("-" * 96)
    print(f"日期入口：handlers 里直接读日期 {len(problems)} 处"
          f"（唯一入口 = src/tasks/training/daycycle.py）")
    for p in problems:
        print("  ✗ " + p)
    return problems


# ---------- 地点模块互不知道（2026-11-01 架构）----------
# 地点模块（places/）只管自己的判断，**不许引用别的地点**。
# 「今天该去哪」是**地图模块**的职责；地点模块之间互相知道 = 耦合又回来了。
def check_places():
    """地点模块之间不许互相引用。

    返回问题列表（空 = 通过）。
    """
    problems = []
    places_dir = os.path.join(ROOT, "src", "tasks", "training", "places")
    if not os.path.isdir(places_dir):
        return problems
    # 模块名 → 它自己的「地名」
    owners = {}
    for f in sorted(os.listdir(places_dir)):
        if not f.endswith(".py") or f == "__init__.py":
            continue
        path = os.path.join(places_dir, f)
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src, filename=path)
        for node in ast.walk(tree):
            # 形如 PLACE = "步行街" 的模块归属声明
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "PLACE":
                        owners[f] = node.value.value
    for f, own_place in owners.items():
        path = os.path.join(places_dir, f)
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src, filename=path)
        # docstring 里说明「为什么」是**需要**写的（记录了具体事故），
        # 所以只查**代码里真正用到的字符串**，不看文档。
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) \
                        and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstrings:
                continue
            text = node.value
            # 地点模块之间不许提别的地点名
            for other_file, other_place in owners.items():
                if other_file == f or other_place == own_place:
                    continue
                if other_place in text and other_place != own_place:
                    problems.append(
                        f"src/tasks/training/places/{f}:{node.lineno} "
                        f"提到了别的地点「{other_place}」——"
                        f"地点模块之间不许互相知道（该由地图模块决定去哪个）")
    print("-" * 96)
    print(f"地点模块：跨地点引用 {len(problems)} 处"
          f"（已归属 {len(owners)} 个地点模块；决策模块 map.py 不算地点，"
          f"它**就是**知道所有地点的那一层）")
    for p in problems:
        print("  ✗ " + p)
    return problems


# ---------- 通用交互层零地点知识（2026-11-01 架构）----------
# `training/interactions.py` 做的是跨地点复用的**动作**。
# 它**不许认识任何地点** —— 兜底坐标、正则、页面特征都得由调用方给。
# 否则通用层会变成「什么都知道一点」的地方
# （2026-11-01 之前的现状：ACTIVITY_GO 一个正则塞了 9 个地点名，
#  leave_street 的兜底坐标是扭蛋页专属的，三个退出方法各写各的坐标）。
def check_interactions():
    """通用交互层不许出现地点名。

    返回问题列表（空 = 通过）。
    """
    problems = []
    path = os.path.join(ROOT, "src", "tasks", "training", "interactions.py")
    if not os.path.exists(path):
        return problems
    # 所有地点名（从 labels.WEEKEND_PLACES 取）+ 公园/餐厅内的活动名
    place_words = set()
    try:
        from src.tasks.ui.labels import WEEKEND_PLACES
        place_words.update(WEEKEND_PLACES)
    except Exception:
        place_words.update(("餐厅", "游戏厅", "公园", "步行街"))
    place_words.update(("划船", "野餐", "散步", "当服务员", "桌游GM"))
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename=path)
    # docstring 里说明「为什么」是**需要**写的（记录了 2026-11-01 的具体事故），
    # 所以只查**代码里真正用到的字符串**，不看文档。
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        # 只看**字符串常量**（注释/docstring 里的地名不算 ——
        # 文档里说明「为什么」是需要写的）
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            for w in place_words:
                if w in node.value:
                    problems.append(
                        f"src/tasks/training/interactions.py:{node.lineno} "
                        f"字符串里出现了地点名「{w}」——"
                        f"通用交互层不认识地点（坐标/正则由调用方传进来）")
    print("-" * 96)
    print(f"通用交互层：地点知识 {len(problems)} 处"
          f"（通用层 = training/interactions.py）")
    for p in problems:
        print("  ✗ " + p)
    return problems


# ---------- 配置项单一来源（2026-11-01 架构）----------
# `training/config_spec.py` 是配置项的**唯一声明**（键/默认值/类型/说明）。
#
# 为什么必须收口（2026-11-01 实测的三个问题）：
#   ① 默认值写在两处 —— `default_config` 声明了 40，调用处又写一遍
#      `config.get("休息精力阈值", 40)`，那个 40 **永远不生效**（死代码）；
#   ② 有配置项用户在 GUI 里配不到（没在 default_config 声明）；
#   ③ 有配置项声明了但没人读（GUI 上的无效项）。
#
# 规则：
#   ✗ 禁止：调用处写默认值（`config.get("键", 默认值)`）—— 默认值只许在 spec 里
#   ✗ 禁止：读一个 spec 里没声明的键 —— 缺键时 config.get 返回 None，
#     而 None 是 falsy → 「允许进步行街」被当成 False（2026-11-01 踩过）
#   ✓ 允许：spec 里声明了、但**没有任何地方读**（要报出来，但可以先留着）
def check_config():
    """配置项的单一来源门禁（**只管 TrainingTask 的配置**）。

    范围：`src/tasks/training/` 下读 `self.config` 的地方。
    别的任务（QuickMatchTask / InputTestTask）有各自的
    `default_config`，不在这个 spec 的范围内 —— 它们自己声明，不归这里管。

    返回问题列表（空 = 通过）。
    """
    problems = []
    spec_path = os.path.join(ROOT, "src", "tasks", "training", "config_spec.py")
    if not os.path.exists(spec_path):
        return problems
    sys.path.insert(0, ROOT)
    from src.tasks.training import config_spec
    declared = {s.key for s in config_spec.ALL}

    read_keys = set()
    src_root = os.path.join(ROOT, "src")
    for dp, _, files in os.walk(src_root):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(dp, f)
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            if rel.endswith("training/config_spec.py"):
                continue
            # 只管 training/ 下的（本任务的业务代码）
            in_scope = rel.startswith("src/tasks/training/")
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                fn = node.func
                if not (isinstance(fn, ast.Attribute) and fn.attr == "get"):
                    continue
                if not (isinstance(fn.value, ast.Attribute) and fn.value.attr == "config"):
                    continue
                if not node.args:
                    continue
                kn = node.args[0]
                if not (isinstance(kn, ast.Constant) and isinstance(kn.value, str)):
                    continue
                key = kn.value
                if in_scope:
                    read_keys.add(key)
                    if len(node.args) >= 2:
                        problems.append(
                            f"{rel}:{node.lineno} config.get({key!r}, 默认值) —— "
                            f"默认值只许写在 training/config_spec.py（调用处那个是死代码）")
                    if key not in declared:
                        problems.append(
                            f"{rel}:{node.lineno} 读了没在 config_spec 里声明的键 {key!r} —— "
                            f"缺键时 config.get 返回 None（falsy），语义会变")

    # 声明了但 training/ 里没人读 —— **警告**（不拦截）。
    # 这些是 2026-10 就存在的死配置项（`只在决战前清空` / `快进重复间隔(秒)`），
    # 不是本次改动造成的；先报出来，等用户决定接上还是删掉。
    # 注意：有些键是**监控页**读的（技能-彩色/金色/白色），不算无效项。
    monitor = os.path.join(ROOT, "src", "ui", "MonitorTab.py")
    if os.path.exists(monitor):
        mtext = open(monitor, encoding="utf-8").read()
        for s in config_spec.ALL:
            if s.key in read_keys:
                continue
            if ('"%s"' % s.key) in mtext:
                read_keys.add(s.key)

    warnings = []
    # 废弃键（2026-11-07 用户口径：多角色队列降级为外部注入参数，任务不再读配置）。
    # 留在 default_config 里只为老存档 verify_config 不炸键，不算无效项。
    deprecated = {"多角色队列", "队长队列"}
    for key in sorted(declared - read_keys):
        if key in deprecated:
            continue
        warnings.append(
            f"src/tasks/training/config_spec.py: 声明了 {key!r} 但 src/ 里没人读 —— "
            f"GUI 上是个无效选项（要么接上，要么删掉）")

    print("-" * 96)
    print(f"配置单一来源：违规 {len(problems)} 处"
          f"（声明 {len(declared)} 项 / 被读 {len(read_keys & declared)} 项）")
    for p in problems:
        print("  ✗ " + p)
    for w in warnings:
        print("  ! " + w)
    return problems


# ---------- 判据单一来源（文案门禁）----------
# 受管按钮词：出现在这些方法的 names 参数里、且是**内联字面量**，就算违规。
# 为什么是这几个：它们是「按钮」，必须全等匹配且来自 labels.BUTTONS
# （见 src/tasks/ui/labels.py 的说明）。页面标记（"跳过比赛"/"失败率"…）不在此列，
# 它们是 match_text 的包含匹配用途，等页面标记迁移时再扩这张表。
MANAGED_WORDS = ("确定", "确认", "决定", "下一步", "关闭", "取消", "放弃")

# names 参数在哪个位置（见 BaseGirlsTask），以及「这个调用是不是要点按钮」。
#   True  = 点击意图，按钮文案必须来自 labels.BUTTONS
#   False = 纯检测（match_text 的用途就是「在不在这一页」），按钮词出现在混合元组里
#           是正常的页面标记（例：('失败率','决定','休息','赢得比赛','VS')）
# exact_text 两用：整词找目标既可能是点击也可能是判页面，所以只在
# 「整条元组都是受管按钮词」（= 它就是个按钮集合）时才拦。
NAME_ARG_INDEX = {
    "find_text": (0, True),
    "click_text": (0, True),
    "click_text_and_expect": (1, True),
    "exact_text": (1, "pure"),
}

ALLOWLIST_PATH = os.path.join(ROOT, "tools", "labels_allowlist.txt")


def load_allowlist():
    """{ '相对路径.py' : {'确定', ...} } —— 棘轮白名单，**只许缩短**。

    格式：一行一条 `相对路径.py|词`，# 开头是注释。空文件 = 全部迁移完成。
    """
    allowed = {}
    if not os.path.exists(ALLOWLIST_PATH):
        return allowed
    for raw in open(ALLOWLIST_PATH, encoding="utf-8"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        path, _, word = line.partition("|")
        allowed.setdefault(path.strip(), set()).add(word.strip())
    return allowed


def check_undefined_names():
    """未定义名字门禁：`foo` 用了但没导入/没定义 —— 只可能是漏 import 的模块级常量。

    2026-11-03 抓到的真实 bug：`handlers/match.py: dump_skill_frame` 用了
    `PROJECT_ROOT` 但只 import 了 `JPEG_PARAMS`，外面套着 `except Exception: log_debug`
    → 技能三选一存帧**从上线起一次都没成功**，日志上只有一行 DEBUG
    `技能帧存证失败: NameError`。跑完整一轮才发现。

    为什么用 ast 不用 pyflakes：本机没装 pyflakes，而且这条只需查「裸名字」
    （混入类全走 `self.xxx`，裸名字几乎只可能是模块级常量/函数），30 行够用。

    只管 `src/` 和 `tools/`（`tests/` 里同名变量多，误报率高）。
    """
    import builtins

    def defined_names(tree):
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                names.update(node.names)
        return names

    problems = []
    for sub in ("src", "tools"):
        for dp, _, files in os.walk(os.path.join(ROOT, sub)):
            for f in sorted(files):
                if not f.endswith(".py"):
                    continue
                path = os.path.join(dp, f)
                rel = os.path.relpath(path, ROOT).replace("\\", "/")
                try:
                    source = open(path, encoding="utf-8").read()
                    tree = ast.parse(source, filename=path)
                except (OSError, SyntaxError) as e:
                    problems.append(f"{rel}: 解析失败 {type(e).__name__}")
                    continue
                known = (defined_names(tree) | set(dir(builtins))
                         | {"__file__", "__name__", "__doc__"})
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                            and node.id not in known):
                        problems.append(f"{rel}:{node.lineno} {node.id}")
    print(f"未定义名字门禁：裸名字用了但没导入/没定义 {len(problems)} 处")
    for item in problems:
        print(f"  - {item}")
    return problems


def check_mojibake():
    """源码里不许出现 U+FFFD —— 那是被 GBK 吃掉的字符，中文已经丢了。

    2026-11-03 用户要求排查 GBK 影响时扫出来的：`src/` 和 `tools/` 共 5 处，
    全在注释里（例如「接手别人…局时」本该是「接手别人的局时」，中间那个字被吃掉）。
    乱码进了源码就**读不回来**（原文已丢），所以把它变成门禁：出现即 exit 1。
    """
    problems = []
    for sub in ("src", "tools"):
        for dp, _, files in os.walk(os.path.join(ROOT, sub)):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(dp, f)
                rel = os.path.relpath(path, ROOT).replace("\\", "/")
                try:
                    text = open(path, encoding="utf-8").read()
                except OSError:
                    continue
                for lineno, line in enumerate(text.splitlines(), 1):
                    if "\ufffd" in line:
                        problems.append(f"{rel}:{lineno}")
    print(f"编码门禁：源码里被 GBK 吃掉的字符（U+FFFD）{len(problems)} 处")
    for item in problems:
        print(f"  - {item}")
    return problems


def check_labels():
    """文案门禁：按钮词不许在调用处写内联字面量，必须走 labels.BUTTONS。

    为什么用 ast 不用 grep：grep 会命中注释、docstring、日志文案
    （这就是「返回出现 139 次」那种统计失真的来源）；ast 只看真正的 names 参数。

    与 check_architecture 同一套哲学：
      ✗ 禁止：把按钮文案写在调用处（数据散落在 153 个点上）
      ✓ 允许：模块级/类级**声明表**（labels.BUTTONS、EXIT_TEXTS、POPUP_TEXTS…）
      ✓ 允许：白名单文件里登记过的历史包袱（每迁移一处删一行）

    返回问题列表（空 = 通过）。
    """
    allow = load_allowlist()
    problems, baselined = [], 0

    for dp, _, files in os.walk(os.path.join(ROOT, "src")):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(dp, f)
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = (fn.attr if isinstance(fn, ast.Attribute)
                        else fn.id if isinstance(fn, ast.Name) else "")
                spec = NAME_ARG_INDEX.get(name)
                if spec is None:
                    continue
                idx, mode = spec
                if len(node.args) <= idx:
                    continue
                arg = node.args[idx]
                literals = [n.value for n in ast.walk(arg)
                            if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                hit = [s for s in literals if s.strip() in MANAGED_WORDS]
                if not hit:
                    continue
                if hit and mode == "pure" and len(hit) != len(literals):
                    continue      # 混合元组 = 页面标记（exact_text 判页面），不是按钮集合
                if set(hit) <= allow.get(rel, set()):
                    baselined += 1
                    continue
                problems.append(
                    f"{rel}:{node.lineno} {name} 里写死了按钮文案 {tuple(hit)} ——"
                    f"改用 self.click_button(\"键名\") / labels.BUTTONS")

    print("-" * 96)
    print(f"文案门禁：按钮文案内联 {len(problems)} 处；白名单内 {baselined} 处"
          f"（{ALLOWLIST_PATH}，只许缩短）")
    for p in problems:
        print("  ✗ " + p)
    return problems


def main():
    utf8_stdout()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    verbose = "--verbose" in sys.argv
    use_cache = "--cache" in sys.argv
    show_all = "--all-hits" in sys.argv
    update = "--update-expected" in sys.argv
    report = []

    if args:
        paths = []
        for a in args:
            paths.extend(glob.glob(os.path.join(a, "*.png")) if os.path.isdir(a) else [a])
    else:
        paths = sorted(glob.glob(os.path.join(ROOT, "docs", "shots", "*.png")))

    if not paths:
        print("没有找到图片")
        return 1

    expected = load_expected()
    ocr_holder = {"ocr": None}
    fails, misses, new_shots, conflicts = [], [], [], []

    print(f"{'图片':<38} {'实际':<24} 期望 / 动作")
    print("-" * 96)
    for path in paths:
        name, hits = probe(path, ocr_holder, use_cache, verbose)
        base = os.path.basename(name)
        first = hits[0][0] if hits else None
        want = expected.get(base)

        if update:
            # 期望值 "-" 表示「这张图不该被任何 handler 认领」（防误判的回归样例）
            expected[base] = first or MISS_MARK
            want = expected[base]

        if first is None:
            if want == MISS_MARK:
                print(f"{base:<38} {'✓ 未命中（预期）':<24} -")
            else:
                misses.append(base)
                print(f"{base:<38} {'✗ 未命中':<24} {want or '-'}")
        elif want == MISS_MARK:
            fails.append((base, MISS_MARK, first))
            print(f"{base:<38} {first:<24} 期望 不命中  ✗ 抢错 {describe(hits[0][1])}")
        elif want is None:
            new_shots.append(base)
            print(f"{base:<38} {first:<24} 新图（未登记） {describe(hits[0][1])}")
        elif first != want:
            fails.append((base, want, first))
            print(f"{base:<38} {first:<24} 期望 {want}  ✗ 抢错")
        else:
            print(f"{base:<38} {first:<24} {describe(hits[0][1])}")

        if show_all and len(hits) > 1:
            conflicts.append((base, [h[0] for h in hits[1:]]))
            for hname, _ in hits[1:]:
                print(f"    + 同时命中 {hname}")

        action = describe(hits[0][1]) if hits else "-"
        report.append(f"{base}\t{first or '-'}\t{want or '-'}\t{action}")

    if update:
        save_expected(expected)
        print(f"已更新 {EXPECTED_PATH}（{len(expected)} 条）")

    print("-" * 96)
    if misses:
        print(f"未命中（{len(misses)}）：" + "、".join(misses))
    if fails:
        print(f"抢错（{len(fails)}）：" + "、".join(
            f"{b} 期望 {w} 实际 {a}" for b, w, a in fails))
    if new_shots:
        print(f"未登记（{len(new_shots)}）：" + "、".join(new_shots))
        print("  确认结果正确后跑：python tools/check_handlers.py --update-expected")
    if show_all and conflicts:
        print(f"多 handler 同时命中（{len(conflicts)}）：")
        for base, others in conflicts:
            print(f"  {base}: 首个 {expected.get(base, '?')}，另外 {'、'.join(others)}")

    arch_problems = check_architecture()
    label_problems = check_labels()
    date_problems = check_date_entry()
    place_problems = check_places()
    inter_problems = check_interactions()
    config_problems = check_config()
    moji_problems = check_mojibake()
    name_problems = check_undefined_names()

    failed = bool(fails or misses or arch_problems or label_problems
                  or date_problems or place_problems or inter_problems
                  or config_problems or moji_problems or name_problems)
    # 中文结果落盘（UTF-8）：stdout 可能被父进程按 cp936 解码（见 tools/_console.py）。
    lines = ["# check_handlers 报告",
             f"结果：{'失败' if failed else '通过'}",
             "# 图片\t实际\t期望\t动作",
             *report]
    report_path = write_report("check_handlers.txt", "\n".join(lines) + "\n")
    print(f"report={report_path}")

    if failed:
        print("回归失败")
        return 1
    print("回归通过")
    return 0

if __name__ == "__main__":
    sys.exit(main())
