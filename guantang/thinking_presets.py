import json

THINKING_PRESETS = [
    {
        "vendor": "DeepSeek",
        "match": ["deepseek-v4", "deepseek-v3", "deepseek-r1"],
        "param": "reasoning_effort",
        "options": [
            {"label": "低", "value": "low"},
            {"label": "高", "value": "high"},
            {"label": "最高", "value": "max"},
        ],
        "default": "high",
        "extra": {"thinking": {"type": "enabled"}},
        "note": "DeepSeek V4 系列，2026-08 官方文档",
    },
    {
        "vendor": "Kimi",
        "match": ["kimi-k3"],
        "param": "reasoning_effort",
        "options": [
            {"label": "低", "value": "low"},
            {"label": "高", "value": "high"},
            {"label": "最高", "value": "max"},
        ],
        "default": "max",
        "note": "K3 始终思考，仅调节强度，2026 官方文档",
    },
    {
        "vendor": "Kimi",
        "match": ["kimi-k2.6", "kimi-k2.5"],
        "param": "thinking.type",
        "options": [
            {"label": "开启", "value": "enabled"},
            {"label": "关闭", "value": "disabled"},
        ],
        "default": "enabled",
        "note": "K2.6 另有 thinking.keep 保留式思考，2026 官方文档",
    },
    {
        "vendor": "智谱 GLM",
        "match": ["glm-5", "glm-4.7", "glm-4.6"],
        "param": "thinking.type",
        "options": [
            {"label": "开启", "value": "enabled"},
            {"label": "关闭", "value": "disabled"},
        ],
        "default": "enabled",
        "note": "GLM-5 系列默认开启思考，2026-08 官方文档",
    },
    {
        "vendor": "OpenAI",
        "match": ["gpt-5", "o3", "o4", "o1"],
        "param": "reasoning_effort",
        "options": [
            {"label": "最低", "value": "minimal"},
            {"label": "低", "value": "low"},
            {"label": "中", "value": "medium"},
            {"label": "高", "value": "high"},
        ],
        "default": "medium",
        "note": "GPT-5 系列含 minimal，o 系列无 minimal",
    },
    {
        "vendor": "Gemini",
        "match": ["gemini"],
        "param": "reasoning_effort",
        "options": [
            {"label": "低", "value": "low"},
            {"label": "中", "value": "medium"},
            {"label": "高", "value": "high"},
        ],
        "default": "medium",
        "note": "Gemini 3.1 Pro 三档",
    },
    {
        "vendor": "Claude",
        "match": ["claude"],
        "param": "thinking.budget_tokens",
        "options": [
            {"label": "关闭", "value": "__none__"},
            {"label": "低", "value": "2048"},
            {"label": "中", "value": "8192"},
            {"label": "高", "value": "16384"},
            {"label": "最高", "value": "32768"},
        ],
        "default": "8192",
        "extra": {"thinking": {"type": "enabled"}},
        "note": "budget_tokens 需搭配 thinking.type=enabled，关闭则不传 thinking",
    },
    {
        "vendor": "通义千问",
        "match": ["qwen3", "qwen2.5"],
        "param": "enable_thinking",
        "options": [
            {"label": "开启", "value": "true"},
            {"label": "关闭", "value": "false"},
        ],
        "default": "true",
        "note": "qwen3.7/3.8 参数待复核",
    },
]


def match_preset(model: str):
    name = (model or "").lower()
    for preset in THINKING_PRESETS:
        for key in preset["match"]:
            if key in name:
                return preset
    return None


def _set_path(payload: dict, path: str, value):
    parts = path.split(".")
    cur = payload
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _deep_merge(target: dict, source: dict):
    for k, v in source.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _deep_merge(target[k], v)
        else:
            target[k] = v


def _coerce(value: str):
    text = value.strip()
    if text == "true":
        return True
    if text == "false":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _apply_manual(inject: dict, text: str, preset):
    text = text.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                _deep_merge(inject, data)
                return
        except json.JSONDecodeError:
            pass
    if "=" in text:
        key, _, value = text.partition("=")
        _set_path(inject, key.strip(), _coerce(value.strip()))
        return
    if preset:
        _set_path(inject, preset["param"], _coerce(text))
        _deep_merge(inject, preset.get("extra") or {})


def build_thinking(model: str, thinking_mode: str = "", thinking_custom: str = ""):
    preset = match_preset(model)
    manual = (thinking_custom or "").strip()
    if preset:
        if manual:
            inject = {}
            _apply_manual(inject, manual, preset)
            return inject or None
        mode = (thinking_mode or "").strip()
        if not mode:
            return None
        if mode == "__none__":
            return None
        valid = [o["value"] for o in preset["options"]]
        if mode not in valid:
            return None
        inject = {}
        _set_path(inject, preset["param"], _coerce(mode))
        _deep_merge(inject, preset.get("extra") or {})
        return inject
    if manual:
        inject = {}
        _apply_manual(inject, manual, None)
        return inject or None
    return None
