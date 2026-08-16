from pathlib import Path

import yaml


class ModeStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / "modes"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _read(self, folder: Path) -> dict:
        data = yaml.safe_load((folder / "mode.yaml").read_text(encoding="utf-8")) or {}
        content = folder / "mode.md"
        data["content"] = content.read_text(encoding="utf-8") if content.exists() else ""
        return data

    def list(self) -> list[dict]:
        result = []
        if not self.dir.exists():
            return result
        for folder in sorted(self.dir.iterdir()):
            if folder.is_dir() and (folder / "mode.yaml").exists():
                result.append(self._read(folder))
        return result

    def get(self, name: str) -> dict | None:
        folder = self.dir / name
        if not (folder / "mode.yaml").exists():
            return None
        return self._read(folder)

    def upsert(self, data: dict) -> dict:
        name = data["name"]
        folder = self.dir / name
        folder.mkdir(parents=True, exist_ok=True)
        meta = {"name": name, "description": data.get("description", "")}
        if data.get("replace_rules"):
            meta["replace_rules"] = data["replace_rules"]
        (folder / "mode.yaml").write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
        (folder / "mode.md").write_text(data.get("content", ""), encoding="utf-8")
        return self.get(name)

    def delete(self, name: str):
        folder = self.dir / name
        if folder.exists():
            import shutil

            shutil.rmtree(folder)
