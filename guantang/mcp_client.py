import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


class MCPManager:
    def __init__(self):
        self.tools: list[dict] = []
        self._entries = []
        self._tool_map = {}

    async def start(self, skill_defs: list[dict]):
        self.tools = []
        for spec in skill_defs:
            if spec.get("type", "mcp") != "mcp":
                continue
            if not spec.get("enabled", True):
                continue
            try:
                await self._connect_one(spec)
            except Exception as e:
                print(f"[MCP] 技能 {spec.get('name', '?')} 连接失败：{e}")

    async def _connect_one(self, spec: dict):
        url = str(spec.get("url") or "").strip()
        if url:
            ctx = streamable_http_client(url)
        else:
            params = StdioServerParameters(
                command=spec["command"],
                args=spec.get("args", []),
                env=spec.get("env"),
            )
            ctx = stdio_client(params)
        read, write = await ctx.__aenter__()
        session_ctx = ClientSession(read, write)
        try:
            session = await session_ctx.__aenter__()
            await session.initialize()
            result = await session.list_tools()
            name = spec.get("name", spec.get("command", "mcp"))
            for t in result.tools:
                tool = {
                    "server": name,
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.input_schema or {"type": "object", "properties": {}},
                }
                self.tools.append(tool)
                self._tool_map[t.name] = session
            print(f"[MCP] 技能 {name} 已连接，工具 {len(result.tools)} 个")
            self._entries.append((ctx, session_ctx, session))
        except BaseException:
            await session_ctx.__aexit__(*sys.exc_info())
            await ctx.__aexit__(*sys.exc_info())
            raise

    async def call_tool(self, name: str, arguments: dict) -> str:
        session = self._tool_map.get(name)
        if session is None:
            return f"错误：未知工具 {name}"
        try:
            result = await session.call_tool(name, arguments)
            parts = []
            for item in result.content:
                if getattr(item, "type", "") == "text":
                    parts.append(item.text)
                else:
                    parts.append(f"[{item.type}]")
            text = "\n".join(parts)
            if result.is_error:
                return f"工具执行出错：{text}"
            return text
        except Exception as e:
            return f"工具调用失败：{e}"

    async def close(self):
        for ctx, session_ctx, _ in reversed(self._entries):
            await session_ctx.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)
        self._entries = []
        self.tools = []
        self._tool_map = {}
