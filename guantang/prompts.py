from pathlib import Path

from .variables import build_values, render


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
        return render(text, build_values(role_name=role_name, player_name=player_name))

    def build_system_prompt(
        self,
        role_setting: str,
        role_name: str,
        player_name: str,
        mode_text: str = "",
        model_name: str = "",
    ) -> str:
        values = build_values(model_name=model_name, role_name=role_name, player_name=player_name)
        parts = []
        if role_setting.strip():
            parts.append(render(role_setting, values))
        if mode_text.strip():
            parts.append(render(mode_text, values))
        return "\n\n".join(parts)
