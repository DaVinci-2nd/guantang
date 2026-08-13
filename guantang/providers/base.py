import asyncio
import json
from dataclasses import dataclass, field

import httpx


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


class ProviderError(Exception):
    pass


class BaseProvider:
    name = "base"
    endpoint_path = "/chat/completions"

    def __init__(self, base_url: str, api_key: str, model: str = "default", timeout: int = 120, max_retries: int = 2):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.model = model
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}{self.endpoint_path}"

    def set_model(self, model: str):
        self.model = model

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _payload(self, messages, tools, temperature, max_tokens, stream: bool = True, thinking=None) -> dict:
        payload = {"model": self.model, "messages": messages, "stream": stream}
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if thinking:
            for key, value in thinking.items():
                payload[key] = value
        return payload

    async def stream_chat(self, messages, tools=None, temperature=None, max_tokens=None, thinking=None):
        payload = self._payload(messages, tools, temperature, max_tokens, thinking=thinking)
        from ..send_log import get_context, send_log

        entry = send_log.start(
            self.endpoint,
            self.model,
            payload.get("messages"),
            payload.get("tools"),
            payload.get("temperature"),
            payload.get("max_tokens"),
            payload.get("thinking"),
            context=get_context(),
        )
        accum = {}
        last_error = None
        try:
            for attempt in range(self.max_retries + 1):
                try:
                    async with self._client.stream(
                        "POST",
                        self.endpoint,
                        headers=self._headers(),
                        json=payload,
                    ) as resp:
                        if resp.status_code != 200:
                            body = (await resp.aread()).decode("utf-8", errors="replace")
                            raise ProviderError(f"HTTP {resp.status_code}：{body[:500]}")
                        async for line in resp.aiter_lines():
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError as e:
                                send_log.append_event(entry, ["warning", f"SSE 数据解析失败：{e}"])
                                continue
                            for event in self._parse_chunk(chunk, accum):
                                send_log.append_event(entry, self._summarize(event))
                                yield event
                        if accum:
                            send_log.append_event(entry, ["warning", "流结束时存在未完成的工具调用，已强制取出"])
                            for event in self._flush_tool_calls(accum):
                                send_log.append_event(entry, self._summarize(event))
                                yield event
                    send_log.finish(entry, ok=True)
                    return
                except (httpx.HTTPError, ProviderError, asyncio.CancelledError) as e:
                    last_error = e
                    if attempt < self.max_retries:
                        await asyncio.sleep(2**attempt)
            raise ProviderError(f"请求失败（已重试 {self.max_retries} 次）：{last_error}")
        except Exception as e:
            send_log.finish(entry, ok=False, error=str(e))
            raise

    @staticmethod
    def _summarize(event: tuple) -> list:
        kind = event[0]
        if kind in ("reasoning", "text"):
            return [kind, event[1][:200]]
        if kind == "tool_call":
            return ["tool_call", event[1].name, event[1].arguments]
        if kind == "tool_result":
            return ["tool_result", event[1][:300]]
        if kind == "done":
            return ["done", event[1]]
        return [kind]

    def _flush_tool_calls(self, accum: dict) -> list:
        events = []
        for idx in sorted(accum):
            slot = accum[idx]
            try:
                args = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": slot["arguments"]}
            events.append(
                ("tool_call", ToolCall(id=slot["id"] or f"call_{idx}", name=slot["name"], arguments=args))
            )
        accum.clear()
        return events

    def _parse_chunk(self, chunk: dict, accum: dict):
        events = []
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            reasoning = delta.get("reasoning_content")
            if reasoning:
                events.append(("reasoning", reasoning))
            content = delta.get("content")
            if content:
                events.append(("text", content))
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = accum.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
            finish = choice.get("finish_reason")
            if finish == "tool_calls":
                events.extend(self._flush_tool_calls(accum))
            elif finish:
                events.append(("done", finish))
        return events

    async def close(self):
        await self._client.aclose()
