import json
import sqlite3
import time
from contextvars import ContextVar

MAX_PER_SESSION = 100
MAX_EVENTS = 200

_send_ctx: ContextVar = ContextVar("guantang_send_ctx", default={})


def set_context(**kwargs):
    _send_ctx.set({**_send_ctx.get(), **kwargs})


def get_context() -> dict:
    return _send_ctx.get()


class SendLog:
    def __init__(self):
        self.db_path = None

    def configure(self, db_path):
        self.db_path = str(db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS send_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "session_id INTEGER NOT NULL DEFAULT 0,"
            "ts REAL NOT NULL,"
            "entry TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

    def start(
        self,
        endpoint: str,
        model: str,
        messages: list,
        tools=None,
        temperature=None,
        max_tokens=None,
        thinking=None,
        context: dict | None = None,
    ) -> dict:
        entry = {
            "ts": time.time(),
            "context": dict(context or {}),
            "endpoint": endpoint,
            "request": {
                "model": model,
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "thinking": thinking,
            },
            "events": [],
            "ok": None,
            "error": None,
        }
        if self.db_path:
            sid = (entry["context"].get("session_id")) or 0
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute(
                "INSERT INTO send_log (session_id, ts, entry) VALUES (?, ?, ?)",
                (sid, entry["ts"], json.dumps(entry, ensure_ascii=False)),
            )
            entry["_log_id"] = cur.lastrowid
            conn.execute(
                "DELETE FROM send_log WHERE session_id=? AND id NOT IN "
                "(SELECT id FROM send_log WHERE session_id=? ORDER BY id DESC LIMIT ?)",
                (sid, sid, MAX_PER_SESSION),
            )
            conn.commit()
            conn.close()
        return entry

    def append_event(self, entry: dict, event: list):
        if len(entry["events"]) < MAX_EVENTS:
            entry["events"].append(event)

    def finish(self, entry: dict, ok: bool = True, error: str | None = None):
        entry["ok"] = ok
        entry["error"] = error
        if self.db_path and entry.get("_log_id"):
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE send_log SET entry=? WHERE id=?",
                (json.dumps(entry, ensure_ascii=False), entry["_log_id"]),
            )
            conn.commit()
            conn.close()

    def for_session(self, session_id) -> list[dict]:
        if not self.db_path:
            return []
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT entry FROM send_log WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, MAX_PER_SESSION),
        ).fetchall()
        conn.close()
        result = []
        for row in rows:
            try:
                entry = json.loads(row[0])
                entry.pop("_log_id", None)
                result.append(entry)
            except (json.JSONDecodeError, TypeError):
                continue
        return result

    def clear(self):
        if self.db_path:
            conn = sqlite3.connect(self.db_path)
            conn.execute("DELETE FROM send_log")
            conn.commit()
            conn.close()


send_log = SendLog()
