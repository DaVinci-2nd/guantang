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
    workdirs TEXT NOT NULL DEFAULT '[]',
    cleared_at REAL NOT NULL DEFAULT 0,
    title_set INTEGER NOT NULL DEFAULT 0,
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
    blocks TEXT NOT NULL DEFAULT '[]',
    interrupted INTEGER NOT NULL DEFAULT 0,
    branch_id INTEGER NOT NULL DEFAULT 0,
    branch_root INTEGER NOT NULL DEFAULT 0,
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
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
            if "title_set" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN title_set INTEGER NOT NULL DEFAULT 0")
            if "workdirs" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN workdirs TEXT NOT NULL DEFAULT '[]'")
            if "cleared_at" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN cleared_at REAL NOT NULL DEFAULT 0")
            mcols = [r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
            if "attachments" not in mcols:
                conn.execute("ALTER TABLE messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
            if "blocks" not in mcols:
                conn.execute("ALTER TABLE messages ADD COLUMN blocks TEXT NOT NULL DEFAULT '[]'")
            if "interrupted" not in mcols:
                conn.execute("ALTER TABLE messages ADD COLUMN interrupted INTEGER NOT NULL DEFAULT 0")
            if "branch_id" not in mcols:
                conn.execute("ALTER TABLE messages ADD COLUMN branch_id INTEGER NOT NULL DEFAULT 0")
            if "branch_root" not in mcols:
                conn.execute("ALTER TABLE messages ADD COLUMN branch_root INTEGER NOT NULL DEFAULT 0")

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
            if not row:
                return None
            session = dict(row)
            try:
                session["workdirs"] = json.loads(session["workdirs"])
            except (json.JSONDecodeError, TypeError):
                session["workdirs"] = []
            return session

    def list_sessions(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
            result = []
            for r in rows:
                s = dict(r)
                try:
                    s["workdirs"] = json.loads(s["workdirs"])
                except (json.JSONDecodeError, TypeError):
                    s["workdirs"] = []
                result.append(s)
            return result

    def update_session(self, session_id: int, title=None, character_name=None, mode=None, title_set=None, workdirs=None) -> dict | None:
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
        if title_set is not None:
            fields.append("title_set = ?")
            values.append(1 if title_set else 0)
        if workdirs is not None:
            fields.append("workdirs = ?")
            values.append(json.dumps(workdirs, ensure_ascii=False))
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
        blocks: list | None = None,
        interrupted: bool = False,
        branch_id: int = 0,
        branch_root: int = 0,
        created_at: float | None = None,
    ) -> dict:
        now = _now()
        ts = float(created_at) if created_at else now
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO messages (session_id, sender, character_name, content, reasoning, tool_events, attachments, blocks, interrupted, branch_id, branch_root, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    sender,
                    character_name,
                    content,
                    reasoning,
                    json.dumps(tool_events or [], ensure_ascii=False),
                    json.dumps(attachments or [], ensure_ascii=False),
                    json.dumps(blocks or [], ensure_ascii=False),
                    1 if interrupted else 0,
                    branch_id,
                    branch_root,
                    ts,
                ),
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
            return self._row_to_message(row)

    def update_message(self, session_id: int, message_id: int, content: str | None = None, blocks: list | None = None, attachments: list | None = None, interrupted: bool | None = None, reasoning: str | None = None, branch_id: int | None = None, branch_root: int | None = None) -> dict | None:
        fields = []
        values = []
        if content is not None:
            fields.append("content = ?")
            values.append(content)
        if blocks is not None:
            fields.append("blocks = ?")
            values.append(json.dumps(blocks, ensure_ascii=False))
        if attachments is not None:
            fields.append("attachments = ?")
            values.append(json.dumps(attachments, ensure_ascii=False))
        if interrupted is not None:
            fields.append("interrupted = ?")
            values.append(1 if interrupted else 0)
        if reasoning is not None:
            fields.append("reasoning = ?")
            values.append(reasoning)
        if branch_id is not None:
            fields.append("branch_id = ?")
            values.append(branch_id)
        if branch_root is not None:
            fields.append("branch_root = ?")
            values.append(branch_root)
        if not fields:
            return self.get_message(session_id, message_id)
        values.append(message_id)
        values.append(session_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE messages SET {', '.join(fields)} WHERE id = ? AND session_id = ?", values)
        return self.get_message(session_id, message_id)

    def get_message(self, session_id: int, message_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ? AND session_id = ?", (message_id, session_id)
            ).fetchone()
            return self._row_to_message(row) if row else None

    def latest_branch_id(self, session_id: int) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COALESCE(MAX(branch_id), 0) FROM messages WHERE session_id = ?", (session_id,)).fetchone()
            return row[0] or 0

    def next_branch_id(self, session_id: int) -> int:
        return self.latest_branch_id(session_id) + 1

    def delete_branch_after(self, session_id: int, message_id: int, branch_id: int):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND id > ? AND branch_id = ?",
                (session_id, message_id, branch_id),
            )

    def mark_branch_root(self, session_id: int, message_id: int, branch_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE messages SET branch_root = ? WHERE session_id = ? AND id > ? AND branch_id = ? AND branch_root = 0",
                (message_id, session_id, message_id, branch_id),
            )

    def delete_messages_after(self, session_id: int, message_id: int):
        now = _now()
        with self._conn() as conn:
            row = conn.execute("SELECT created_at FROM messages WHERE id = ? AND session_id = ?", (message_id, session_id)).fetchone()
            cleared_at = row["created_at"] if row else now
            conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND id > ?", (session_id, message_id)
            )
            conn.execute("UPDATE sessions SET cleared_at = ?, updated_at = ? WHERE id = ?", (cleared_at, now, session_id))

    def clear_session_messages(self, session_id: int):
        now = _now()
        with self._conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("UPDATE sessions SET cleared_at = ?, updated_at = ? WHERE id = ?", (now, now, session_id))

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
        try:
            msg["blocks"] = json.loads(msg["blocks"])
        except (json.JSONDecodeError, TypeError):
            msg["blocks"] = []
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
