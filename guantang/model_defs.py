from pathlib import Path

import yaml


class ModelStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / "models"
        self.dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict]:
        result = []
        for path in sorted(self.dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            result.append(data)
        return result

    def get(self, name: str) -> dict | None:
        path = self.dir / f"{name}.yaml"
        if not path.exists():
            return None
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def upsert(self, data: dict) -> dict:
        name = data["name"]
        path = self.dir / f"{name}.yaml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return data

    def delete(self, name: str):
        path = self.dir / f"{name}.yaml"
        if path.exists():
            path.unlink()
