import json

from .mcp_client import MCPManager
from .providers.base import ToolCall
from .zh_translator import ZhTranslator


class Engine:
    def __init__(
        self,
        provider,
        mcp: MCPManager,
        translator: ZhTranslator,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        self.provider = provider
        self.mcp = mcp
        self.translator = translator
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def run(self, system_prompt: str, player_message: str, history: list[dict] | None = None):
        messages = list(history or [])
        messages.append({"role": "user", "content": player_message})
        async for event in self.run_messages(system_prompt, messages):
            yield event

    async def run_messages(self, system_prompt: str, messages: list[dict]):
        messages = [{"role": "system", "content": system_prompt}] + list(messages)

        while True:
            openai_tools = await self._build_openai_tools()

            tool_calls: list[ToolCall] = []
            reply_chunks = []
            async for event in self.provider.stream_chat(
                messages,
                tools=openai_tools,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
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
                result = await self.mcp.call_tool(tc.name, tc.arguments)
                yield ("tool_result", tc.name, result)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": result}
                )

        yield ("end", messages)

    async def _build_openai_tools(self) -> list[dict]:
        result = []
        seen = set()
        for tool in self.mcp.tools:
            if tool["name"] in seen:
                continue
            seen.add(tool["name"])
            zh = await self.translator.translate_tool(tool)
            result.append(self.translator.to_openai_tool(tool, zh))
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
