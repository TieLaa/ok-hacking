# ok-hacking

Automation for the Windows client of **Basketball Girls: Leap to the Top**, built on [ok-script](https://github.com/ok-oldking/ok-script).

[中文](../index.md) · [GitHub](https://github.com/TieLaa/ok-hacking) · [README](https://github.com/TieLaa/ok-hacking/blob/main/README_en.md)

!!! warning "Disclaimer"
    Not affiliated with the game's publisher. For study and personal use only. Automating an online game may violate its terms of service; bans are possible and are your own risk.

## What this is

An automation program that plays the career loop: it recognizes the game UI, decides from configured rules, and runs a full career until the season ends. Everything is local image/OCR logic; no LLM calls at runtime.

## Quick start

1. Download an installer from [Releases](https://github.com/TieLaa/ok-hacking/releases), or run from source as administrator;
2. Confirm the capture page can grab the game window;
3. On the task page expand “自动训练”, adjust options, press start;
4. Watch the monitor page. When stuck, read `logs/debug/<time>_<tag>/ocr.txt` first.

See the [README](https://github.com/TieLaa/ok-hacking/blob/main/README_en.md) for install and run details.

## Engineering docs

Chinese only at the moment:

- [Architecture](../ARCHITECTURE.md)
- [Engineering rules](../ENGINEERING.md)
- [Monitor page](../UI_MONITOR.md)
- [UI map](../UI_MAP.md)
- [MCP dev loop](../MCP_DEV.md)
- [Packaging and release](../release.md)

## License

Code is [MIT](https://github.com/TieLaa/ok-hacking/blob/main/LICENSE). Third-party components (ok-script, PyAppify, PyQt-Fluent-Widgets) keep their own licenses; see the [README](https://github.com/TieLaa/ok-hacking/blob/main/README_en.md#license).
