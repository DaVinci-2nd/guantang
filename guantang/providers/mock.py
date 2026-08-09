import json

from .base import BaseProvider, ToolCall

TRANSLATION_MARKER = "请把下面的工具说明翻译成中文"


class MockProvider(BaseProvider):
    name = "mock"

    def __init__(self, model: str = "mock-chat", timeout: int = 30, max_retries: int = 0):
        super().__init__("http://mock.local", "mock", model=model, timeout=timeout, max_retries=max_retries)

    async def stream_chat(self, messages, tools=None, temperature=None, max_tokens=None):
        last = messages[-1]
        content = last.get("content") or ""
        if last.get("role") == "tool":
            yield ("reasoning", "（模拟）工具结果已经拿到，整理一下再回复。")
            yield ("text", "工具执行完毕，结果我已经收到，一切正常喵。")
            yield ("done", "stop")
            return
        if content.startswith(TRANSLATION_MARKER):
            name = content.split("工具名称：")[-1].splitlines()[0].strip()
            text = json.dumps(
                {"description": f"{name} 的中文用途说明（模拟翻译）", "properties": {}}, ensure_ascii=False
            )
            yield ("text", text)
            yield ("done", "stop")
            return
        yield ("reasoning", "（模拟）让我想想这个任务该用什么工具。")
        if tools:
            first = tools[0]
            yield ("text", "这个任务我来调用工具处理喵。")
            yield ("tool_call", ToolCall(id="mock_1", name=first["function"]["name"], arguments={}))
            yield ("done", "tool_calls")
        else:
            yield ("text", "（模拟）好的喵，任务收到，我会好好干的。")
            yield ("done", "stop")
