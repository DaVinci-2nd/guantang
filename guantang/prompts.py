from pathlib import Path


class PromptAssembler:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _file_text(self, relative: str) -> str:
        path = self.root / relative
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def translator_text(self) -> str:
        return self._file_text("prompts/translator.md")

    def cli_system_text(self) -> str:
        return self._file_text("prompts/system.md")

    @staticmethod
    def render_markers(text: str, role_name: str, player_name: str) -> str:
        text = text.replace("{{角色名}}", role_name).replace("{{角色}}", role_name)
        return text.replace("{{玩家名}}", player_name).replace("{{玩家}}", player_name)

    def build_system_prompt(
        self,
        role_setting: str,
        role_name: str,
        player_name: str,
        mode_text: str = "",
    ) -> str:
        parts = []
        if role_setting.strip():
            parts.append(self.render_markers(role_setting, role_name, player_name))
        if mode_text.strip():
            parts.append(self.render_markers(mode_text, role_name, player_name))
        return "\n\n".join(parts)
