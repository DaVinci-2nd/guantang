import json

from .cards import convert_macros


def parse_assistant(data: bytes) -> dict:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Cherry 助手文件不是有效的 JSON")
    records = parsed if isinstance(parsed, list) else [parsed]
    records = [r for r in records if isinstance(r, dict)]
    if not records:
        raise ValueError("Cherry 助手文件内容为空")
    record = records[0]
    name = str(record.get("name") or "").strip()
    prompt = str(record.get("prompt") or "").strip()
    if not name or not prompt:
        raise ValueError("Cherry 助手缺少 name 或 prompt 字段")
    description = str(record.get("description") or "").strip()
    parts = []
    parts.append("【系统提示词】\n" + convert_macros(prompt))
    if description:
        parts.append("【描述】\n" + convert_macros(description))
    return {"name": name, "setting": "\n\n".join(parts)}
