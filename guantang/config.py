import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    def __init__(self, path=None):
        load_dotenv(BASE_DIR / ".env")
        self.path = Path(path) if path else BASE_DIR / "config.yaml"
        self.path = self.path.resolve()
        self.root = self.path.parent
        source = self.path
        if not source.exists():
            example = self.root / "config.example.yaml"
            if example.exists():
                source = example
        with open(source, encoding="utf-8") as f:
            self.data = yaml.safe_load(f) or {}

    def __getattr__(self, name):
        if name == "provider":
            return os.environ.get("GUANTANG_PROVIDER") or self.data.get("provider", "deepseek")
        if name in self.data:
            return self.data[name]
        raise AttributeError(name)

    def get(self, name, default=None):
        return self.data.get(name, default)

    def api_key(self, variable="DEEPSEEK_API_KEY"):
        return os.environ.get(variable, "")

    def player(self) -> dict:
        p = self.data.get("player") or {}
        return {"name": p.get("name", "") or "Untitled", "avatar": p.get("avatar", "")}

    def ui(self) -> dict:
        u = self.data.get("ui") or {}
        return {
            "theme": u.get("theme", "dark"),
            "sidebar_left": u.get("sidebar_left", True),
            "sidebar_right": u.get("sidebar_right", True),
            "centered": u.get("centered", True),
        }

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, allow_unicode=True, sort_keys=False)

    def set_player(self, name=None, avatar=None):
        p = self.data.setdefault("player", {})
        if name is not None:
            p["name"] = name
        if avatar is not None:
            p["avatar"] = avatar

    def set_ui(self, theme=None, sidebar_left=None, sidebar_right=None, centered=None):
        u = self.data.setdefault("ui", {})
        if theme is not None:
            u["theme"] = theme
        if sidebar_left is not None:
            u["sidebar_left"] = sidebar_left
        if sidebar_right is not None:
            u["sidebar_right"] = sidebar_right
        if centered is not None:
            u["centered"] = centered

    def multimodal(self) -> dict:
        m = self.data.get("multimodal") or {}
        return {
            "enabled": m.get("enabled", False),
            "model": m.get("model", ""),
            "prompt": m.get("prompt", ""),
        }

    def set_multimodal(self, enabled=None, model=None, prompt=None):
        m = self.data.setdefault("multimodal", {})
        if enabled is not None:
            m["enabled"] = enabled
        if model is not None:
            m["model"] = model
        if prompt is not None:
            m["prompt"] = prompt

    def auto_title(self) -> dict:
        a = self.data.get("auto_title") or {}
        return {
            "enabled": a.get("enabled", False),
            "model": a.get("model", ""),
            "prompt": a.get("prompt", ""),
            "mode": a.get("mode", 1),
            "rounds": a.get("rounds", 3),
        }

    def set_auto_title(self, enabled=None, model=None, prompt=None, mode=None, rounds=None):
        a = self.data.setdefault("auto_title", {})
        if enabled is not None:
            a["enabled"] = enabled
        if model is not None:
            a["model"] = model
        if prompt is not None:
            a["prompt"] = prompt
        if mode is not None:
            a["mode"] = mode
        if rounds is not None:
            a["rounds"] = rounds

    def revise(self) -> dict:
        r = self.data.get("revise") or {}
        return {
            "model": r.get("model", ""),
            "system_prompt": r.get("system_prompt", ""),
            "prompt": r.get("prompt", ""),
            "scope": r.get("scope", "all"),
        }

    def set_revise(self, model=None, system_prompt=None, prompt=None, scope=None):
        r = self.data.setdefault("revise", {})
        if model is not None:
            r["model"] = model
        if system_prompt is not None:
            r["system_prompt"] = system_prompt
        if prompt is not None:
            r["prompt"] = prompt
        if scope is not None:
            r["scope"] = scope
