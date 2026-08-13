import json

from .builtin_tools import build_edit_diff, describe_operation, execute_builtin, to_openai_tools
from .mcp_client import MCPManager
from .providers.base import ToolCall
from .search import search
from .zh_translator import ZhTranslator

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "联网搜索互联网获取实时信息。当需要查询最新新闻、实时动态、事实验证或模型自身知识无法覆盖的内容时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或完整问题"},
                "max_results": {"type": "integer", "description": "返回结果数量，默认 5，最大 10"},
            },
            "required": ["query"],
        },
    },
}


class ApprovalStopped(Exception):
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = arguments


class Engine:
    def __init__(
        self,
        provider,
        mcp: MCPManager,
        translator: ZhTranslator,
        temperature: float | None = None,
        max_tokens: int | None = None,
        search_skills: list[dict] | None = None,
        builtin_loader=None,
        approval_handler=None,
        workdirs: list[str] | None = None,
    ):
        self.provider = provider
        self.mcp = mcp
        self.translator = translator
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.search_skills = search_skills or []
        self.builtin_loader = builtin_loader
        self.approval_handler = approval_handler
        self.workdirs = workdirs if workdirs is not None else []

    async def run(self, system_prompt: str, player_message: str, history: list[dict] | None = None, thinking=None):
        messages = list(history or [])
        messages.append({"role": "user", "content": player_message})
        async for event in self.run_messages(system_prompt, messages, thinking=thinking):
            yield event

    async def run_messages(self, system_prompt: str, messages: list[dict], thinking=None):
        messages = [{"role": "system", "content": system_prompt}] + list(messages)

        while True:
            builtin_defs = self._load_builtin_defs()
            openai_tools = await self._build_openai_tools(builtin_defs)

            tool_calls: list[ToolCall] = []
            reply_chunks = []
            async for event in self.provider.stream_chat(
                messages,
                tools=openai_tools,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                thinking=thinking,
            ):
                if event[0] in ("reasoning", "text"):
                    if event[0] == "text":
                        reply_chunks.append(event[1])
                    yield event
                elif event[0] == "tool_call":
                    tool_calls.append(event[1])
                    yield event
                else:
                    yield event

            if not tool_calls:
                reply = "".join(reply_chunks)
                if reply:
                    messages.append({"role": "assistant", "content": reply})
                break

            messages.append(self._assistant_tool_message(tool_calls))
            for tc in tool_calls:
                yield ("tool_exec", tc.name, tc.arguments)
                if tc.name == "web_search":
                    result = await self._call_web_search(tc.arguments)
                elif any(t["name"] == tc.name for t in builtin_defs):
                    result = await self._call_builtin(tc.name, tc.arguments, builtin_defs)
                else:
                    result = await self.mcp.call_tool(tc.name, tc.arguments)
                yield ("tool_result", tc.name, result)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": result}
                )

        yield ("end", messages)

    async def _call_web_search(self, arguments: dict) -> str:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return "搜索失败：缺少搜索关键词"
        try:
            max_results = int(arguments.get("max_results") or 5)
        except (TypeError, ValueError):
            max_results = 5
        max_results = max(1, min(max_results, 10))
        skill = self.search_skills[0] if self.search_skills else {}
        result = await search(
            skill.get("provider", "tavily"),
            query,
            skill.get("api_key", ""),
            skill.get("base_url", ""),
            max_results,
        )
        return result[:4000]

    async def _call_builtin(self, name: str, arguments: dict, builtin_defs: list[dict]) -> str:
        tool_def = next((t for t in builtin_defs if t["name"] == name), None)
        operation = await describe_operation(name, arguments, tool_def)
        if tool_def and tool_def.get("approval") and self.approval_handler:
            diff = build_edit_diff(arguments, self.workdirs) if name == "edit_text" else None
            decision = await self.approval_handler(name, arguments, operation, diff)
            if decision == "reject":
                return f"该操作已被拒绝。\n被拒绝的操作：{operation}"
            if decision == "reject_stop":
                raise ApprovalStopped(name, arguments)
        return await execute_builtin(name, arguments, self.workdirs)

    def _load_builtin_defs(self) -> list[dict]:
        if not self.builtin_loader:
            return []
        defs = self.builtin_loader()
        return defs or []

    async def _build_openai_tools(self, builtin_defs: list[dict] | None = None) -> list[dict]:
        builtin_defs = builtin_defs if builtin_defs is not None else self._load_builtin_defs()
        result = []
        seen = set()
        for tool in self.mcp.tools:
            if tool["name"] in seen:
                continue
            seen.add(tool["name"])
            zh = await self.translator.translate_tool(tool)
            result.append(self.translator.to_openai_tool(tool, zh))
        for tool in to_openai_tools(builtin_defs):
            name = tool["function"]["name"]
            if name in seen:
                continue
            seen.add(name)
            result.append(tool)
        if self.search_skills and "web_search" not in seen:
            result.append(WEB_SEARCH_TOOL)
        return result

    @staticmethod
    def _assistant_tool_message(tool_calls: list[ToolCall]) -> dict:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ],
        }
