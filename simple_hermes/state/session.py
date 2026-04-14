from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import List, Dict, Any, Callable

from simple_hermes.config import DB_PATH, DEFAULT_SESSION_ID


class SessionStore:
    """Tiny SQLite session store with a bit of session lineage and FTS recall."""

    def _infer_session_type(self, session_id: str, session_type: str | None) -> str:
        if session_type:
            return session_type
        if "/cont-" in session_id:
            return "continuation"
        if "/child-" in session_id:
            return "child"
        return "root"

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        cols = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        names = {row[1] for row in cols}
        if column not in names:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                parent_session_id TEXT,
                title TEXT,
                session_type TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                kind TEXT,
                tool_name TEXT,
                created_at REAL NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                session_id UNINDEXED,
                content,
                content=''
            );

            CREATE TABLE IF NOT EXISTS session_state (
                session_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (session_id, key)
            );
            """
        )
        self._ensure_column("messages", "kind", "TEXT")
        self._ensure_column("messages", "tool_name", "TEXT")
        self._ensure_column("sessions", "parent_session_id", "TEXT")
        self._ensure_column("sessions", "title", "TEXT")
        self._ensure_column("sessions", "session_type", "TEXT")
        self.conn.commit()
        self.ensure_session(DEFAULT_SESSION_ID, session_type="root")

    def ensure_session(
        self,
        session_id: str,
        parent_session_id: str | None = None,
        title: str | None = None,
        session_type: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO sessions (id, created_at, parent_session_id, title, session_type) VALUES (?, ?, ?, ?, ?)",
            (session_id, time.time(), parent_session_id, title, session_type),
        )
        if parent_session_id is not None or title is not None or session_type is not None:
            self.conn.execute(
                "UPDATE sessions SET parent_session_id = COALESCE(parent_session_id, ?), title = COALESCE(title, ?), session_type = COALESCE(session_type, ?) WHERE id = ?",
                (parent_session_id, title, session_type, session_id),
            )
        self.conn.commit()

    def create_child_session(self, parent_session_id: str, title: str | None = None) -> str:
        child_id = f"{parent_session_id}/child-{uuid.uuid4().hex[:10]}"
        self.ensure_session(child_id, parent_session_id=parent_session_id, title=title, session_type="child")
        return child_id

    def create_continuation_session(self, parent_session_id: str, title: str | None = None) -> str:
        session_id = f"{parent_session_id}/cont-{uuid.uuid4().hex[:10]}"
        self.ensure_session(session_id, parent_session_id=parent_session_id, title=title or "continuation", session_type="continuation")
        return session_id

    def session_info(self, session_id: str) -> Dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id, created_at, parent_session_id, title, session_type FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["session_type"] = self._infer_session_type(data["id"], data.get("session_type"))
        return data

    def rename_session(self, session_id: str, title: str) -> None:
        self.ensure_session(session_id)
        self.conn.execute(
            "UPDATE sessions SET title = ? WHERE id = ?",
            (title.strip(), session_id),
        )
        self.conn.commit()

    def child_sessions(self, parent_session_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, created_at, parent_session_id, title, session_type FROM sessions WHERE parent_session_id = ? ORDER BY created_at ASC",
            (parent_session_id,),
        ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["session_type"] = self._infer_session_type(data["id"], data.get("session_type"))
            out.append(data)
        return out

    def descendants(self, session_id: str) -> List[Dict[str, Any]]:
        seen: List[Dict[str, Any]] = []
        queue = [session_id]
        while queue:
            parent = queue.pop(0)
            children = self.child_sessions(parent)
            seen.extend(children)
            queue.extend(child["id"] for child in children)
        return seen

    def continuation_descendants(self, session_id: str) -> List[Dict[str, Any]]:
        seen: List[Dict[str, Any]] = []
        queue = [session_id]
        while queue:
            parent = queue.pop(0)
            children = self.child_sessions(parent)
            continuations = [child for child in children if child.get("session_type") == "continuation"]
            seen.extend(continuations)
            queue.extend(child["id"] for child in continuations)
        return seen

    def latest_continuation_or_self(self, session_id: str) -> str:
        self.ensure_session(session_id)
        candidates = [self.session_info(session_id), *self.continuation_descendants(session_id)]
        candidates = [candidate for candidate in candidates if candidate is not None]
        latest = max(candidates, key=lambda row: row.get("created_at") or 0)
        return latest["id"]

    def descendants_text(self, session_id: str) -> str:
        rows = self.descendants(session_id)
        if not rows:
            return f"No descendant sessions for: {session_id}"
        lines = [f"Descendant sessions for {session_id}:"]
        for row in rows:
            lines.append(f"- {row['id']} [{row.get('session_type') or 'unknown'}] {row.get('title') or '(untitled)'}")
        return "\n".join(lines)

    def recent_sessions(self, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, created_at, parent_session_id, title, session_type FROM sessions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["session_type"] = self._infer_session_type(data["id"], data.get("session_type"))
            out.append(data)
        return out

    def recent_sessions_text(self, limit: int = 10) -> str:
        rows = self.recent_sessions(limit=limit)
        if not rows:
            return "No sessions yet."
        lines = ["Recent sessions:"]
        for row in rows:
            parent = row.get("parent_session_id") or "-"
            lines.append(f"- {row['id']} [{row.get('session_type') or 'unknown'}] parent={parent} title={row.get('title') or '(untitled)'}")
        return "\n".join(lines)

    def lineage(self, session_id: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        current = self.session_info(session_id)
        while current is not None:
            out.append(current)
            parent = current.get("parent_session_id")
            current = self.session_info(parent) if parent else None
        return list(reversed(out))

    def lineage_text(self, session_id: str) -> str:
        chain = self.lineage(session_id)
        if not chain:
            return f"No session info for: {session_id}"
        lines = ["Session lineage:"]
        for item in chain:
            stype = item.get("session_type") or "unknown"
            title = item.get("title") or "(untitled)"
            lines.append(f"- {item['id']} [{stype}] {title}")
        return "\n".join(lines)

    def append(
        self,
        role: str,
        content: str,
        session_id: str = DEFAULT_SESSION_ID,
        *,
        kind: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        self.ensure_session(session_id)
        self.conn.execute(
            "INSERT INTO messages (session_id, role, content, kind, tool_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, content, kind, tool_name, time.time()),
        )
        self.conn.execute(
            "INSERT INTO messages_fts (session_id, content) VALUES (?, ?)",
            (session_id, content),
        )
        self.conn.commit()

    def set_state(self, session_id: str, key: str, value: str) -> None:
        self.ensure_session(session_id)
        self.conn.execute(
            """
            INSERT INTO session_state (session_id, key, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id, key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (session_id, key, value, time.time()),
        )
        self.conn.commit()

    def get_state(self, session_id: str, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM session_state WHERE session_id = ? AND key = ?",
            (session_id, key),
        ).fetchone()
        return str(row[0]) if row else None

    def delete_state(self, session_id: str, key: str) -> None:
        self.conn.execute(
            "DELETE FROM session_state WHERE session_id = ? AND key = ?",
            (session_id, key),
        )
        self.conn.commit()

    def history(self, session_id: str = DEFAULT_SESSION_ID, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT role, content, kind, tool_name, created_at FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def history_text(self, session_id: str = DEFAULT_SESSION_ID, limit: int = 20) -> str:
        rows = self.history(session_id=session_id, limit=limit)
        if not rows:
            return "No history yet."
        lines = []
        for row in rows:
            prefix = f"[{row['role']}]"
            if row.get("tool_name"):
                prefix += f"<{row['tool_name']}>"
            if row.get("kind"):
                prefix += f"({row['kind']})"
            lines.append(f"{prefix} {row['content']}")
        return "\n".join(lines)

    def search(self, query: str, session_id: str = DEFAULT_SESSION_ID, limit: int = 10) -> List[Dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        try:
            rows = self.conn.execute(
                """
                SELECT m.role, m.content, m.kind, m.tool_name, m.created_at
                FROM messages_fts f
                JOIN messages m ON m.content = f.content AND m.session_id = f.session_id
                WHERE f.session_id = ? AND messages_fts MATCH ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (session_id, query, limit),
            ).fetchall()
            if rows:
                return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            pass
        rows = self.conn.execute(
            "SELECT role, content, kind, tool_name, created_at FROM messages WHERE session_id = ? AND content LIKE ? ORDER BY id DESC LIMIT ?",
            (session_id, f"%{query}%", limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def message_count(self, session_id: str = DEFAULT_SESSION_ID) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    def search_text(
        self,
        query: str,
        session_id: str = DEFAULT_SESSION_ID,
        limit: int = 10,
        summarizer: Callable[[str, List[Dict[str, Any]]], str] | None = None,
    ) -> str:
        rows = self.search(query=query, session_id=session_id, limit=limit)
        if not rows:
            return f"No history results for: {query}"
        snippets = []
        for row in reversed(rows):
            prefix = f"[{row['role']}]"
            if row.get("tool_name"):
                prefix += f"<{row['tool_name']}>"
            if row.get("kind"):
                prefix += f"({row['kind']})"
            snippets.append(f"{prefix} {row['content']}")
        if summarizer is not None:
            headline = summarizer(query, rows)
        else:
            headline = "; ".join(row['content'][:80] for row in rows[:3])
        return "\n".join([
            f"Focused recall summary for '{query}':",
            headline,
            "",
            "Supporting snippets:",
            *snippets,
        ])
