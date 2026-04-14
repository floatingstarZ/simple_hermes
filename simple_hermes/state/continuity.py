from __future__ import annotations

from typing import List, Dict, Any

from simple_hermes.state.session import SessionStore


class ContinuityView:
    """Read-only continuity/browser helpers layered on top of SessionStore.

    This keeps browsing/rendering concerns separate from raw persistence,
    which is closer to how Hermes separates storage from higher-level recall
    and continuity behavior.
    """

    def __init__(self, sessions: SessionStore) -> None:
        self.sessions = sessions

    def session_info(self, session_id: str) -> Dict[str, Any] | None:
        return self.sessions.session_info(session_id)

    def lineage(self, session_id: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        current = self.sessions.session_info(session_id)
        while current is not None:
            out.append(current)
            parent = current.get("parent_session_id")
            current = self.sessions.session_info(parent) if parent else None
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

    def descendants(self, session_id: str) -> List[Dict[str, Any]]:
        seen: List[Dict[str, Any]] = []
        queue = [session_id]
        while queue:
            parent = queue.pop(0)
            children = self.sessions.child_sessions(parent)
            seen.extend(children)
            queue.extend(child["id"] for child in children)
        return seen

    def descendants_text(self, session_id: str) -> str:
        rows = self.descendants(session_id)
        if not rows:
            return f"No descendant sessions for: {session_id}"
        lines = [f"Descendant sessions for {session_id}:"]
        for row in rows:
            lines.append(f"- {row['id']} [{row.get('session_type') or 'unknown'}] {row.get('title') or '(untitled)'}")
        return "\n".join(lines)

    def recent_sessions(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self.sessions.recent_sessions(limit=limit)

    def recent_sessions_text(self, limit: int = 10) -> str:
        rows = self.recent_sessions(limit=limit)
        if not rows:
            return "No sessions yet."
        lines = ["Recent sessions:"]
        for row in rows:
            parent = row.get("parent_session_id") or "-"
            lines.append(f"- {row['id']} [{row.get('session_type') or 'unknown'}] parent={parent} title={row.get('title') or '(untitled)'}")
        return "\n".join(lines)
