import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    character_name TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    sender TEXT NOT NULL,
    character_name TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    reasoning TEXT NOT NULL DEFAULT '',
    tool_events TEXT NOT NULL DEFAULT '[]',
    attachments TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
"""


def _now() -> float:
    return time.time()


class Storage:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
            if "attachments" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")

    def create_session(self, character_name: str = "", mode: str = "") -> dict:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO sessions (title, character_name, mode, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("", character_name, mode, now, now),
            )
            session_id = cur.lastrowid
        return self.get_session(session_id)

    def get_session(self, session_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return dict(row) if row else None

    def list_sessions(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
            return [dict(r) for r in rows]

    def update_session(self, session_id: int, title=None, character_name=None, mode=None) -> dict | None:
        fields = []
        values = []
        if title is not None:
            fields.append("title = ?")
            values.append(title)
        if character_name is not None:
            fields.append("character_name = ?")
            values.append(character_name)
        if mode is not None:
            fields.append("mode = ?")
            values.append(mode)
        if fields:
            fields.append("updated_at = ?")
            values.append(_now())
            values.append(session_id)
            with self._conn() as conn:
                conn.execute(f"UPDATE sessions SET {', '.join(fields)} WHERE id = ?", values)
        return self.get_session(session_id)

    def delete_session(self, session_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def add_message(
        self,
        session_id: int,
        sender: str,
        content: str,
        reasoning: str = "",
        tool_events: list | None = None,
        character_name: str = "",
        attachments: list | None = None,
    ) -> dict:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO messages (session_id, sender, character_name, content, reasoning, tool_events, attachments, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    sender,
                    character_name,
                    content,
                    reasoning,
                    json.dumps(tool_events or [], ensure_ascii=False),
                    json.dumps(attachments or [], ensure_ascii=False),
                    now,
                ),
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
            return self._row_to_message(row)

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> dict:
        msg = dict(row)
        try:
            msg["tool_events"] = json.loads(msg["tool_events"])
        except (json.JSONDecodeError, TypeError):
            msg["tool_events"] = []
        try:
            msg["attachments"] = json.loads(msg["attachments"])
        except (json.JSONDecodeError, TypeError):
            msg["attachments"] = []
        return msg

    def list_messages(self, session_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)
            ).fetchall()
            return [self._row_to_message(r) for r in rows]

    def session_characters(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT character_name FROM sessions WHERE character_name != '' ORDER BY character_name"
            ).fetchall()
            return [r["character_name"] for r in rows]

    def session_titles(self, session_id: int) -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return row["title"] if row else ""
