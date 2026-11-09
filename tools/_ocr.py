"""tools 侧 OCR 的唯一入口：引擎构造 + 结果解析只在这里写一次。

为什么要有这一层（2026-11-09 整理）
    `check_handlers.py`、`ocr_dump.py`、`probe_gacha.py` 各自抄了一份

        ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, show_log=False)

    外加同样的 15 行 paddle 结果解析，区别只是输出形状（6 元组 / Box 对象）。
    参数一旦漂了，「离线门禁看到的」和「dump 出来的」就不是同一个东西；
    而且每帧新建引擎 = 每帧重载一次模型。

用法
    from tools import _ocr
    _ocr.items(frame)      # [(text, x, y, w, h, score), …]
    _ocr.boxes(frame)      # [Box(x=…, y=…, width=…, height=…, name=text), …]
    _ocr.engine()          # 进程内单例，需要自己再调参数时才用

    坐标一律取**外接矩形**（`min/max`），与 check_handlers 的期望帧缓存一致。
"""

MIN_SCORE = 0.5

_ENGINE = None


def engine():
    """进程内单例：模型只加载一次。"""
    global _ENGINE
    if _ENGINE is None:
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr

        _ENGINE = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, show_log=False)
    return _ENGINE


def items(frame, eng=None, min_score=MIN_SCORE):
    """对一帧跑 OCR，返回 `[(text, x, y, w, h, score), …]`（外接矩形）。"""
    eng = eng or engine()
    result = eng.ocr(frame)
    if not result:
        return []
    page = result[0] if isinstance(result, list) and len(result) == 1 else result
    out = []
    for entry in page or []:
        try:
            box = entry[0]
            text, score = entry[1]
        except Exception:
            continue
        if not text or score is None or score < min_score:
            continue
        xs = [int(p[0]) for p in box]
        ys = [int(p[1]) for p in box]
        out.append((text, min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys), float(score)))
    return out


def boxes(frame, eng=None, min_score=MIN_SCORE, with_confidence=True):
    """同上，但返回 ok-script 的 `Box` 对象（handler 判据吃的形状）。

    `with_confidence=False` 时 Box.confidence 保持 None —— `probe_gacha.py` 原来
    就是这么构造的，保持一致以免影响它的打分路径。
    """
    from ok.feature.Box import Box

    return [Box(x=x, y=y, width=w, height=h, name=text,
                confidence=score if with_confidence else None)
            for text, x, y, w, h, score in items(frame, eng, min_score)]
