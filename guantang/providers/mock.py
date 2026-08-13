import json

from .base import BaseProvider, ToolCall

TRANSLATION_MARKER = "请把下面的工具说明翻译成中文"


class MockProvider(BaseProvider):
    name = "mock"

    def __init__(self, model: str = "mock-chat", timeout: int = 30, max_retries: int = 0):
        super().__init__("http://mock.local", "mock", model=model, timeout=timeout, max_retries=max_retries)

    async def stream_chat(self, messages, tools=None, temperature=None, max_tokens=None, thinking=None):
        from ..send_log import get_context, send_log

        entry = send_log.start(self.endpoint, self.model, messages, tools, temperature, max_tokens, thinking, context=get_context())
        try:
            async for event in self._mock_events(messages, tools):
                send_log.append_event(entry, self._summarize(event))
                yield event
            send_log.finish(entry, ok=True)
        except Exception as e:
            send_log.finish(entry, ok=False, error=str(e))
            raise

    async def _mock_events(self, messages, tools):
        last = messages[-1]
        content = last.get("content") or ""
        if isinstance(content, list):
            text_parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            content = "\n".join(text_parts)
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
            yield ("tool_call", ToolCall(id="mock_1", name=first["function"]["name"], arguments={"query": "mock 测试搜索"}))
            yield ("done", "tool_calls")
        else:
            yield ("text", "（模拟）好的喵，任务收到，我会好好干的。")
            yield ("done", "stop")
