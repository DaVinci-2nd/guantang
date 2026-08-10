import shutil
from pathlib import Path

import yaml

YAML_FIELDS = ["name", "avatar", "model", "thinking_mode", "thinking_strength", "thinking_custom", "temperature", "max_tokens", "skills", "modes", "default_mode"]

INVALID_NAME_CHARS = set('/\\:*?"<>|')


def validate_name(name: str):
    if not name or not name.strip():
        raise ValueError("名称不能为空")
    if any(ch in INVALID_NAME_CHARS for ch in name):
        raise ValueError("名称不能包含 / \\ : * ? \" < > | 字符")


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _dump_yaml(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


class RoleStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / "roles"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _read(self, folder: Path) -> dict:
        data = _load_yaml(folder / "role.yaml")
        setting = folder / "role.md"
        data["setting"] = setting.read_text(encoding="utf-8") if setting.exists() else ""
        avatar = data.get("avatar", "")
        data["has_avatar_file"] = bool(avatar) and (folder / avatar).is_file()
        return data

    def list(self) -> list[dict]:
        result = []
        if not self.dir.exists():
            return result
        for folder in sorted(self.dir.iterdir()):
            if folder.is_dir() and (folder / "role.yaml").exists():
                result.append(self._read(folder))
        return result

    def get(self, name: str) -> dict | None:
        folder = self.dir / name
        if not (folder / "role.yaml").exists():
            return None
        return self._read(folder)

    def create(self, data: dict) -> dict:
        validate_name(data["name"])
        folder = self.dir / data["name"]
        folder.mkdir(parents=True, exist_ok=True)
        _dump_yaml(folder / "role.yaml", {k: v for k, v in data.items() if k in YAML_FIELDS and v is not None})
        setting = data.get("setting", "")
        if setting:
            (folder / "role.md").write_text(setting, encoding="utf-8")
        return self.get(data["name"])

    def update(self, name: str, data: dict) -> dict | None:
        validate_name(data["name"])
        folder = self.dir / name
        if not (folder / "role.yaml").exists():
            return None
        new_name = data.get("name", name)
        if new_name != name:
            new_folder = self.dir / new_name
            if new_folder.exists():
                raise ValueError(f"角色 {new_name} 已存在")
            shutil.move(str(folder), str(new_folder))
            folder = new_folder
            name = new_name
        saved = {k: v for k, v in data.items() if k in YAML_FIELDS and v is not None}
        saved["name"] = name
        _dump_yaml(folder / "role.yaml", saved)
        if "setting" in data:
            (folder / "role.md").write_text(data["setting"], encoding="utf-8")
        return self.get(name)

    def save_setting(self, name: str, text: str):
        folder = self.dir / name
        (folder / "role.md").write_text(text, encoding="utf-8")

    def save_avatar(self, name: str, filename: str, content: bytes) -> str:
        folder = self.dir / name
        folder.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            raise ValueError("头像仅支持 png/jpg/webp/gif")
        avatar_name = f"avatar{suffix}"
        (folder / avatar_name).write_bytes(content)
        return avatar_name

    def delete(self, name: str):
        folder = self.dir / name
        if folder.exists():
            shutil.rmtree(folder)
