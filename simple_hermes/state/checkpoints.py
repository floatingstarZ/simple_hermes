from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from simple_hermes.config import BASE_DIR

MAX_CHECKPOINT_FILES = 500
MAX_CHECKPOINT_FILE_CHARS = 200_000
SKIP_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules", ".venv", "venv", "test_results", "traces"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".tar", ".gz"}


@dataclass
class CheckpointRecord:
    checkpoint_id: str
    created_at: float
    reason: str
    file_count: int
    path: Path


class CheckpointStore:
    """Small project-scoped text snapshot store for local rollback."""

    def __init__(self, project_root: Path, base_dir: Path | None = None) -> None:
        self.project_root = project_root.resolve()
        digest = hashlib.sha1(str(self.project_root).encode("utf-8")).hexdigest()[:16]
        self.base_dir = (base_dir or BASE_DIR) / "checkpoints" / digest
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _checkpoint_path(self, checkpoint_id: str) -> Path:
        return self.base_dir / f"{checkpoint_id}.json"

    def _should_skip(self, path: Path) -> bool:
        if any(part in SKIP_PARTS for part in path.parts):
            return True
        if path.name.startswith(".") and path.name not in {".env"}:
            return True
        return path.suffix.lower() in SKIP_SUFFIXES

    def _iter_project_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.project_root.rglob("*"):
            if path.is_dir() or self._should_skip(path):
                continue
            files.append(path)
            if len(files) >= MAX_CHECKPOINT_FILES:
                break
        return files

    def create(self, reason: str = "manual") -> CheckpointRecord:
        checkpoint_id = time.strftime("cp-%Y%m%d-%H%M%S")
        candidate = self._checkpoint_path(checkpoint_id)
        counter = 1
        while candidate.exists():
            checkpoint_id = f"{time.strftime('cp-%Y%m%d-%H%M%S')}-{counter}"
            candidate = self._checkpoint_path(checkpoint_id)
            counter += 1

        files: dict[str, str] = {}
        skipped: list[str] = []
        for path in self._iter_project_files():
            try:
                rel = str(path.relative_to(self.project_root))
                content = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if len(content) > MAX_CHECKPOINT_FILE_CHARS:
                skipped.append(rel)
                continue
            files[rel] = content

        payload = {
            "id": checkpoint_id,
            "created_at": time.time(),
            "project_root": str(self.project_root),
            "reason": reason.strip() or "manual",
            "files": files,
            "skipped": skipped,
        }
        candidate.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return CheckpointRecord(
            checkpoint_id=checkpoint_id,
            created_at=payload["created_at"],
            reason=payload["reason"],
            file_count=len(files),
            path=candidate,
        )

    def _load(self, checkpoint_id: str) -> dict[str, Any] | None:
        path = self._checkpoint_path(checkpoint_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_records(self, limit: int = 10) -> list[CheckpointRecord]:
        records: list[CheckpointRecord] = []
        for path in self.base_dir.glob("cp-*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            records.append(
                CheckpointRecord(
                    checkpoint_id=str(data.get("id") or path.stem),
                    created_at=float(data.get("created_at") or 0),
                    reason=str(data.get("reason") or ""),
                    file_count=len(data.get("files") or {}),
                    path=path,
                )
            )
        records.sort(key=lambda record: (record.created_at, record.checkpoint_id), reverse=True)
        if limit > 0:
            records = records[:limit]
        return records

    def latest_id(self) -> str | None:
        records = self.list_records(limit=1)
        return records[0].checkpoint_id if records else None

    def restore(self, checkpoint_id: str | None = None) -> str:
        target_id = checkpoint_id or self.latest_id()
        if not target_id:
            return "No checkpoints found."
        data = self._load(target_id)
        if data is None:
            return f"Checkpoint not found: {target_id}"
        if Path(str(data.get("project_root", ""))).resolve() != self.project_root:
            return f"Checkpoint {target_id} belongs to a different project root."

        files: dict[str, str] = data.get("files") or {}
        restored = 0
        for rel, content in files.items():
            path = (self.project_root / rel).resolve()
            try:
                path.relative_to(self.project_root)
            except ValueError:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(content), encoding="utf-8")
            restored += 1

        removed = 0
        known = set(files)
        for path in self._iter_project_files():
            try:
                rel = str(path.relative_to(self.project_root))
            except ValueError:
                continue
            if rel in known:
                continue
            try:
                path.unlink()
                removed += 1
            except Exception:
                pass
        return f"Restored checkpoint {target_id}: restored {restored} files, removed {removed} files created after the checkpoint."
