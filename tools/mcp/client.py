"""MCP 命令行客户端：给「不支持原生 MCP 连接的 agent」用。

它做三件事，全部走官方 SDK：

1. 从 ``logs/mcp/connections/*.json`` 找到活着的实例（校验 PID + 监听端口）；
2. 建 streamable-http 会话（bearer token 从连接文件读，不打印）；
3. 可选提交一个任务文件并轮询到终态，打印 status 里的结果。

用法：

    python tools/mcp/client.py                          # 只列工具/已注册任务
    python tools/mcp/client.py tools/mcp/probe.py       # 跑探针并等结果
    python tools/mcp/client.py --reload                 # 热重载所有任务
"""

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

TERMINAL = ("succeeded", "failed", "cancelled")


def find_connection(project_root=PROJECT_ROOT):
    """挑一个真正活着的连接记录：PID 存在且该进程在监听记录里的端口。"""
    import psutil

    directory = Path(project_root) / "logs" / "mcp" / "connections"
    candidates = sorted(directory.glob("*.json")) if directory.is_dir() else []
    snapshot = Path(project_root) / "logs" / "mcp" / "connection.json"
    if snapshot.is_file():
        candidates.append(snapshot)
    for path in reversed(candidates):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            url = record["url"]
            port = int(url.rsplit(":", 1)[1].split("/")[0])
            process = psutil.Process(int(record["pid"]))
            listening = any(
                conn.status == psutil.CONN_LISTEN
                and conn.laddr.ip == "127.0.0.1" and conn.laddr.port == port
                for conn in process.net_connections(kind="tcp"))
            if listening:
                return {"url": url, "token": record["token"], "pid": record["pid"],
                        "file": str(path)}
        except Exception:                        # noqa: BLE001
            continue
    raise SystemExit(
        "没有活着的 MCP 服务。先起：\n"
        "  .venv\\Scripts\\python.exe -m ok mcp --config src.config:config")


def _json_items(result):
    """FastMCP 对列表是**一项一个 content**，不是单个 JSON 数组。"""
    items = []
    for block in result.content:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            items.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return items


async def _poll(session, run_id, timeout=600.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last = None
    while loop.time() < deadline:
        result = await session.call_tool("get_run_status", {"run_id": run_id})
        record = json.loads(result.content[0].text)
        state = record.get("state")
        if state != last:
            print(f"  [{state}] phase={record.get('phase')}")
            last = state
        if state in TERMINAL:
            return record
        await asyncio.sleep(2)
    return {"state": "timeout", "run_id": run_id}


async def main(args):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    connection = find_connection()
    print(f"连接 {connection['url']}（pid {connection['pid']}）")

    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {connection['token']}"}, timeout=60))
        streams = await stack.enter_async_context(streamable_http_client(
            connection["url"], http_client=http))
        session = await stack.enter_async_context(
            ClientSession(streams[0], streams[1]))
        await session.initialize()

        tools = await session.list_tools()
        print("工具:", ", ".join(t.name for t in tools.tools))

        tasks = _json_items(await session.call_tool("list_tasks", {}))
        if isinstance(tasks, dict):
            tasks = tasks.get("tasks", [tasks])
        print(f"已注册任务（{len(tasks)}）:")
        for item in tasks:
            if not isinstance(item, dict):
                continue
            print(f"  {item.get('task_id')}\t{item.get('kind')}\t"
                  f"enabled={item.get('enabled')}\t{item.get('name')}")

        if args.stop:
            r = await session.call_tool("stop_task", {"run_id": args.stop})
            text = r.content[0].text if r.content else ""
            print("停止请求:", str(text)[:400])
            return 0

        if args.exit_runtime:
            await session.call_tool("exit_process", {})
            print("已请求 runtime 退出")
            return 0

        if args.reload:
            result = await session.call_tool("reload_tasks", {})
            text = result.content[0].text if result.content else ""
            print("重载结果:", str(text)[:800])

        if args.task:
            path = str(Path(args.task).resolve())
            for round_index in range(args.repeat):
                if args.repeat > 1:
                    print(f"===== 第 {round_index + 1}/{args.repeat} 轮 =====")
                result = await session.call_tool("run_task", {"file_path": path})
                run_id = json.loads(result.content[0].text)["run_id"]
                print(f"提交 {path} -> {run_id}")
                record = await _poll(session, run_id, timeout=args.timeout)
                print("结果:", json.dumps(record, ensure_ascii=False)[:1500])
                if record.get("state") != "succeeded":
                    return 1
            return 0
        return 0


def cli():
    parser = argparse.ArgumentParser(description="ok-script MCP 命令行客户端")
    parser.add_argument("task", nargs="?", help="要提交的任务文件（绝对路径或相对仓库）")
    parser.add_argument("--reload", action="store_true", help="热重载所有任务")
    parser.add_argument("--exit", dest="exit_runtime", action="store_true",
                        help="要求 MCP runtime 自行退出（管理员进程用普通 kill 杀不掉）")
    parser.add_argument("--stop", metavar="RUN_ID", help="停止指定 run")
    parser.add_argument("--timeout", type=float, default=600.0, help="等待终态的秒数")
    parser.add_argument("--repeat", type=int, default=1,
                        help="连续提交同一任务几轮（跑完一轮再起下一轮）")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args)))


if __name__ == "__main__":
    cli()
