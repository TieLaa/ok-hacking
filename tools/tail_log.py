"""只打印本次运行的关键日志（过滤 OCR/窗口尺寸噪声）。

用法：
    python tools/tail_log.py            # 本次运行的全部关键日志
    python tools/tail_log.py 40         # 只看最后 40 行
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "ok-script.log")

NOISE = (
    "ocr_zone", "ocr result negative", "update_window_size", "StartTab",
    "DeviceManager", "hwnd_window", "gpu_driver", "Config:init",
    "removing duplicate", "Window geometry", "openvino", "FeatureSet",
)


def main():
    tail = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    with io.open(LOG, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    start = max((i for i, l in enumerate(lines) if "run one-time task without ui" in l), default=0)
    out = []
    for line in lines[start:]:
        if any(n in line for n in NOISE):
            continue
        out.append(line.rstrip()[:200])
    if tail:
        out = out[-tail:]
    print("\n".join(out))


if __name__ == "__main__":
    main()
