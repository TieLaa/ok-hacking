# ok-hacking

Automation for the Windows client of **Basketball Girls: Leap to the Top** (篮球少女：飞跃巅峰), built on [ok-script](https://github.com/ok-oldking/ok-script).

> Thanks to [ok-oldking](https://github.com/ok-oldking) for open-sourcing the ok-script framework, and to
> ok-ww / ok-end-field for the engineering practices this project borrows from.

[中文](README.md) · [Docs](docs/en/index.md)

> Not affiliated with the game's publisher. For study and personal use only. Automating an online game may violate its terms of service; bans are possible and are your own risk.

## What it does

- **Training loop**: plays a full career run — picks subjects on training days, handles weekend activities, deals with match days — until the season ends
- **Monitor page**: week, stamina, six stats and score prediction in one screen. Layout lives in `configs/monitor_layout.json`, styling in `assets/ui/monitor.*.qss`; both reload on save without restarting
- **29 task options**: failure-rate cap, rest threshold, designated captain, park choice, shop strategy, gacha quality priority, match-day handling, skill tags… all adjustable in the GUI
- **Stuck recovery**: counts frames that do not change after a click; takes over when a day exceeds the limit, and stops with evidence after three failed takeovers
- **MCP dev loop**: edit task source → `reload_tasks` → verify without restarting the GUI

## Requirements

| Item | Requirement |
| --- | --- |
| OS | Windows 10 / 11 |
| Game | 16:9 client area, minimum 1280×720 (developed on 1920×1080) |
| Privileges | **Administrator.** The game process is elevated; a non-elevated process cannot post input to it |
| Install path | **ASCII only.** ok-script refuses non-ASCII paths |
| Window | **Must stay visible.** When fully occluded or minimized, capture returns no new frames |

## Install

### Option 1: installer

Download from [Releases](https://github.com/TieLaa/ok-hacking/releases):

- `ok-hacking-win32-China-setup.exe` — dependencies from a China mirror
- `ok-hacking-win32-Global-setup.exe` — dependencies from PyPI

The installer requests elevation (`uac: true`). The first launch initializes the OCR model (OpenVINO) and takes a while.

### Option 2: from source

```powershell
git clone https://github.com/TieLaa/ok-hacking.git
cd ok-hacking
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-deps -r requirements.txt
```

`requirements.txt` is a full pip-compile lock, so `--no-deps` is required.

## Run

| Entry point | Purpose |
| --- | --- |
| `main.py` | Normal mode, no recognition boxes |
| `main_debug.py` | Debug mode: verbose logs and recognition overlay |
| `main_gui_mcp.py` | GUI plus the MCP bridge, for hot-reload development |

```powershell
# Without elevation: capture only, input will not reach the game
.\.venv\Scripts\python.exe main_debug.py

# With elevation (run from an elevated terminal)
Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "main_debug.py" -WorkingDirectory (Get-Location) -Verb RunAs
```

## Usage

1. Open the capture page and confirm a screenshot of the game window works.
2. On the task page, expand “自动训练” (Auto Training) and adjust options. **Leaving the captain empty means takeover-only**: it runs matches you started by hand and stops on setup screens instead of starting them.
3. Press start and switch to the monitor page.
4. When stuck, read text first: `logs/debug/<time>_<tag>/ocr.txt` (text + coordinates) and `actions.txt` usually show the blocking step; only look at `frame.jpg` when OCR misses a button.

`configs/TrainingTask.json` is the single source of truth for runtime config (the GUI writes it).

## Layout

| Path | Purpose |
| --- | --- |
| `src/config.py` | Window, capture/interaction backends, task registration, MCP |
| `src/tasks/training/` | All business logic of the training loop (flow / parsers / scoring / handlers / places) |
| `src/tasks/training/handlers/__init__.py` | The single handler priority table |
| `src/interaction/GirlsInteraction.py` | Real-mouse input backend for this game (elevated, brings window to front) |
| `src/ui/` | Monitor page (`MonitorTab.py` collects data, `monitor_view.py` renders) |
| `configs/` | Runtime configuration (not tracked) |
| `docs/` | Engineering docs; `docs/shots/` holds the expected frames for handler regression |
| `tools/` | Ops and debugging scripts; no business judgement here |
| `logs/` | Runtime logs and debug bundles (not tracked) |

## Development

```powershell
# Structural, copy and config-single-source gates; must exit 0
.\.venv\Scripts\python.exe tools\check_handlers.py --cache

# Unit tests
Get-ChildItem tests\*.py | ForEach-Object { .\.venv\Scripts\python.exe -m unittest $_.FullName }
```

See `docs/ARCHITECTURE.md`, `docs/ENGINEERING.md`, `docs/MCP_DEV.md`.

## License

This project's code is [MIT](LICENSE).

Third-party components:

- [ok-script](https://github.com/ok-oldking/ok-script) — Apache License 2.0 + Commons Clause and additional terms; the automation framework ([site](https://ok-script.com/))
- [PyAppify](https://github.com/ok-oldking/pyappify) — GPL-3.0; launcher and updater embedded in the installer
- [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) / PySide6 — GUI components

## Disclaimer

Not affiliated with the game's publisher. For study and personal use only. Automating the game may violate its terms of service and lead to account penalties; you bear all risk. Do not use it for paid boosting, account rental, or cheat development.
