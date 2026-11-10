@echo off
rem ---------------------------------------------------------------
rem  Start ok-hacking GUI with admin rights.
rem
rem  Admin is required: ok-script start_controller calls
rem  restart_admin and closes the app when `not is_admin()` on a
rem  windows device (measured 2026-11-03), and UIPI blocks clicks
rem  from a low-integrity process into the elevated game window.
rem
rem  Usage:
rem    start_gui.bat        main_debug.py    debug mode (dumps shots)
rem    start_gui.bat mcp    main_gui_mcp.py  debug + MCP server
rem                         (run_task / reload_tasks while GUI runs)
rem    main.py is the normal (non-debug) entry, run it directly.
rem
rem  KEEP THIS FILE PURE ASCII. cmd.exe reads it in code page 936;
rem  UTF-8 Chinese comments eat the line break and swallow the next
rem  command (measured 2026-11-03: `cd /d ...` got eaten, working
rem  directory stayed elsewhere, python.exe was not found).
rem  Same reason as the encoding gate in tools/check_handlers.py.
rem ---------------------------------------------------------------
setlocal
set "ROOT=%~dp0"
set "ENTRY=main_debug.py"
if /i "%~1"=="mcp" set "ENTRY=main_gui_mcp.py"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process -FilePath '%ROOT%.venv\Scripts\python.exe' -ArgumentList '%ENTRY%' -WorkingDirectory '%ROOT%' -Verb RunAs"
endlocal
