import json
from pathlib import Path

from .providers.mock import TRANSLATION_MARKER

DEFAULT_TRANSLATOR = "你是一名专业的工具文档中文翻译器。\n你只输出一个 JSON 对象，不输出任何其它内容。"


def _build_request(tool: dict) -> str:
    props = tool.get("inputSchema", {}).get("properties", {})
    prop_lines = []
    for name, spec in props.items():
        desc = spec.get("description", "")
        enum = spec.get("enum")
        extra = f"（可选值：{', '.join(map(str, enum))}）" if enum else ""
        prop_lines.append(f"- {name}：{desc}{extra}")
    props_text = "\n".join(prop_lines) if prop_lines else "（无参数）"
    return (
        f"{TRANSLATION_MARKER}\n"
        f"工具名称：{tool['name']}\n"
        f"工具描述：{tool.get('description', '')}\n"
        f"参数说明：\n{props_text}\n\n"
        "输出格式：\n"
        '{"description": "工具用途的中文描述", "properties": {"参数名": "参数的中文说明"}}'
    )


def _extract_json(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


class ZhTranslator:
    def __init__(self, provider, cache_file: str = "data/zh_cache.json", translator_file: str | Path | None = None):
        self.provider = provider
        self.cache_file = Path(cache_file)
        self.translator_file = Path(translator_file) if translator_file else None
        self.cache = self._load()

    def _load(self) -> dict:
        if self.cache_file.exists():
            try:
                return json.loads(self.cache_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _save(self):
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def _system_text(self) -> str:
        if self.translator_file and self.translator_file.exists():
            return self.translator_file.read_text(encoding="utf-8")
        return DEFAULT_TRANSLATOR

    async def translate_tool(self, tool: dict) -> dict:
        name = tool["name"]
        if name in self.cache:
            return self.cache[name]
        translated = await self._request_translation(tool)
        self.cache[name] = translated
        self._save()
        return translated

    async def _request_translation(self, tool: dict) -> dict:
        messages = [
            {"role": "system", "content": self._system_text()},
            {"role": "user", "content": _build_request(tool)},
        ]
        text = ""
        async for event in self.provider.stream_chat(messages, temperature=0.1):
            if event[0] == "text":
                text += event[1]
        parsed = _extract_json(text) or {}
        return {
            "description": parsed.get("description") or tool.get("description") or tool["name"],
            "properties": parsed.get("properties") or {},
        }

    def to_openai_tool(self, tool: dict, zh: dict) -> dict:
        schema = dict(tool.get("inputSchema", {}))
        properties = {}
        for name, spec in (schema.get("properties") or {}).items():
            copy = dict(spec)
            zh_desc = zh.get("properties", {}).get(name)
            if zh_desc:
                copy["description"] = zh_desc
            properties[name] = copy
        schema["properties"] = properties
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": zh.get("description") or tool.get("description") or "",
                "parameters": schema,
            },
        }
