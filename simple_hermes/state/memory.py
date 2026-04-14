from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

from simple_hermes.config import MEMORY_PATH, USER_MEMORY_PATH


@dataclass
class MemoryEntry:
    text: str


class MemoryStore:
    """Tiny persistent memory store with two buckets.

    This mirrors full Hermes in a very small way:
    - general durable facts about the environment/project
    - user profile facts/preferences
    """

    def __init__(self, memory_path: Path | None = None, user_path: Path | None = None) -> None:
        self.memory_path = memory_path or MEMORY_PATH
        self.user_path = user_path or USER_MEMORY_PATH
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.user_path.parent.mkdir(parents=True, exist_ok=True)
        self._memory_entries: List[MemoryEntry] = []
        self._user_entries: List[MemoryEntry] = []
        self.load()

    def _load_path(self, path: Path) -> List[MemoryEntry]:
        entries: List[MemoryEntry] = []
        if not path.exists():
            return entries
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                entries.append(MemoryEntry(line))
        return entries

    def load(self) -> None:
        self._memory_entries = self._load_path(self.memory_path)
        self._user_entries = self._load_path(self.user_path)

    def _save_path(self, path: Path, entries: List[MemoryEntry]) -> None:
        payload = "\n".join(entry.text for entry in entries)
        path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")

    def save(self) -> None:
        self._save_path(self.memory_path, self._memory_entries)
        self._save_path(self.user_path, self._user_entries)

    def _add_to(self, text: str, entries: List[MemoryEntry], label: str) -> str:
        text = text.strip()
        if not text:
            return f"{label} entry cannot be empty."
        if any(entry.text == text for entry in entries):
            return f"{label} already contains that entry."
        entries.append(MemoryEntry(text))
        self.save()
        return f"Saved {label.lower()}: {text}"

    def add(self, text: str) -> str:
        return self._add_to(text, self._memory_entries, "Memory")

    def add_user(self, text: str) -> str:
        return self._add_to(text, self._user_entries, "User memory")

    def list_text(self) -> str:
        if not self._memory_entries:
            return "No general memories saved yet."
        lines = ["Saved general memories:"]
        for i, entry in enumerate(self._memory_entries, start=1):
            lines.append(f"{i}. {entry.text}")
        return "\n".join(lines)

    def list_user_text(self) -> str:
        if not self._user_entries:
            return "No user memories saved yet."
        lines = ["Saved user memories:"]
        for i, entry in enumerate(self._user_entries, start=1):
            lines.append(f"{i}. {entry.text}")
        return "\n".join(lines)

    def general_count(self) -> int:
        return len(self._memory_entries)

    def user_count(self) -> int:
        return len(self._user_entries)

    def as_prompt_block(self) -> str:
        blocks: List[str] = []
        if self._memory_entries:
            blocks.append(
                "Known durable facts:\n" + "\n".join(f"- {entry.text}" for entry in self._memory_entries)
            )
        if self._user_entries:
            blocks.append(
                "Known user preferences:\n" + "\n".join(f"- {entry.text}" for entry in self._user_entries)
            )
        return "\n\n".join(blocks)
