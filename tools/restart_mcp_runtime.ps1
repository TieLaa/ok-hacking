# 重启 MCP runtime（管理员）。
# 处理三件事：
#   1. 杀掉占 8765 的旧进程（TIME_WAIT 连接没有 OwningProcess，必须过滤空值，
#      否则会报 "无法将参数绑定到参数 Id，因为该参数是空值"）；
#   2. 清掉 15 分钟内、路径为空或属于 ok-hacking 的 python 残渣
#      （ok 用单实例互斥量，旧进程假死会导致新实例起不来）；
#   3. 启动新 runtime。
Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.OwningProcess -and $_.OwningProcess -gt 0 } |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Get-Process python -ErrorAction SilentlyContinue |
    Where-Object {
        $_.StartTime -gt (Get-Date).AddMinutes(-15) -and
        (-not $_.Path -or $_.Path -like '*ok-hacking*')
    } |
    ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 3
Set-Location 'D:\workspace\ok-hacking'
$env:PYTHONIOENCODING = 'utf-8'
& .\.venv\Scripts\python.exe -m ok mcp --config src.config:config *> logs\mcp\runtime.log
