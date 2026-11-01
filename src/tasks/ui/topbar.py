"""顶栏右上角图标条的判读：倍速档位（0=OFF / 1x / 2x / 3x / …）。

四个图标的含义与坐标见 `docs/UI_MAP.md`「顶栏右上角图标条（倍速 / 历史记录 / 菜单 / 返回）」：

    ① 倍速     ▶/Nx     点击在 1x → 2x → 3x → OFF → 1x 之间**一档一档**循环
    ② 历史记录  纸页      事件流水（只读、安全）
    ③ 菜单     ☰        保存后中断 / 放弃 / …（**禁止点击**）
    ④ 返回     箭头      返回上一级

倍速图标的画法（2026-11-01 逐档实测 + 10-26 旧帧对照，见 `logs/topbar/`）：

- **图标里画几个三角 = 这个存档当前解锁到几档**（只解锁到 2x 时画两个三角，解锁 3x 后画三个）。
- **实心（蓝）的三角个数 = 当前档位**；没实心的那些是**白色空心三角**。
- 所以「**还有空心三角 → 没到顶；一个空心都没有 → 已经到该存档的最高档**」。
  这条判据**不需要知道上限是 2 还是 3 还是 4**，游戏以后再加 4x/5x 也不用改代码。
- OFF 态整个图标变成灰字「OFF / 加速」，没有三角 —— 像素上和「全实心」极像（实测 solid 0.52 / hollow 0.01，
  与 3x 的 0.51 / 0.00 无法区分），所以 **OFF 必须靠 OCR 文字判**，见 `off_text()`。

这里只放**纯函数**（帧 / OCR 结果 → 状态），不依赖 ok-script 运行时，方便用 `logs/topbar/` 里的整帧离线校验。

关于阈值：本文件里的数字都是**UI 版式阈值**（三角在屏幕上画在哪、白色空心有多亮），
不是游戏进度量（上限、解锁、次数）。游戏以后加档位只会改变「三角个数」，不会改变这些版式阈值。
"""

# 三个三角所在的一行（含可能出现第 4/5 个三角的余量），相对坐标
SPEED_TRI_BAND = (0.742, 0.012, 0.786, 0.048)
# 「还有空心三角」的判据（UI 版式阈值）：实测 1x 0.25 / 2x 0.12 / 3x 0.00，取 0.05 分界
SPEED_HOLLOW_MIN = 0.05

# OFF 态的文字（实测见过 OFF / 0FF；「加速」是图标下方第二行）
OFF_TEXTS = ("OFF", "0FF", "加速")
OFF_REGION = (0.70, 0.00, 1.00, 0.10)      # 只有右上角这块里的 OFF 才算倍速图标的 OFF 态


def tri_band_stats(frame):
    """返回 (实心蓝占比, 空心占比)。frame 为 BGR，判不了返回 None。"""
    if frame is None:
        return None
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = SPEED_TRI_BAND
    roi = frame[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
    if roi.size == 0:
        return None
    b = roi[:, :, 0].astype(int)
    g = roi[:, :, 1].astype(int)
    r = roi[:, :, 2].astype(int)
    solid = (b - r > 12) & (b > 140) & (b < 243)      # 实心三角
    hollow = (b < 246) & (g < 244) & ((b - r) <= 12)  # 白色空心三角（含浅灰描边）
    return float(solid.mean()), float(hollow.mean())


def speed_at_max(frame):
    """已经到本存档解锁的最高档？（还有空心三角 = 没到顶）

    判不了返回 None —— 调用方**不要**把 None 当成 False 去乱点。
    """
    stats = tri_band_stats(frame)
    if stats is None:
        return None
    _solid, hollow = stats
    return hollow < SPEED_HOLLOW_MIN


def off_text(texts, width, height):
    """当前帧的右上角有没有「OFF / 加速」字样（倍速图标的 OFF 态）。

    texts 是 ok-script 的 OCR 结果（.name / .x / .y / .width / .height，像素）。
    必须限定右上角：正文、商店筛选条里也会出现「OFF」，全局找会误判。
    """
    if not texts or not width or not height:
        return False
    x1, y1, x2, y2 = OFF_REGION
    for t in texts:
        name = (getattr(t, "name", "") or "").strip()
        if not name or not any(k in name for k in OFF_TEXTS):
            continue
        cx = (t.x + t.width / 2) / width
        cy = (t.y + t.height / 2) / height
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            return True
    return False


def speed_label(texts, width, height):
    """右上角读到的档位文字（`1x` / `2x` / `3x` / `OFF` 归一化后），没有就 None。

    只用来**记录和日志**：策略不依赖它（OCR 小字会漏，实测 2x 偶尔读不出）。
    """
    if not texts or not width or not height:
        return None
    x1, y1, x2, y2 = OFF_REGION
    best = None
    for t in texts:
        name = (getattr(t, "name", "") or "").strip()
        if not name:
            continue
        cx = (t.x + t.width / 2) / width
        cy = (t.y + t.height / 2) / height
        if not (x1 <= cx <= x2 and y1 <= cy <= y2):
            continue
        if any(k in name for k in ("OFF", "0FF")):
            return "OFF"
        low = name.lower().replace(" ", "")
        if low and low[-1] == "x" and low[:-1].isdigit():
            best = low[:-1] + "x"
    return best
