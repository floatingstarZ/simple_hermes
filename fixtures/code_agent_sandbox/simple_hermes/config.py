from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "simple_hermes"
BASE_DIR = Path.home() / ".simple_hermes"
BASE_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_PATH = BASE_DIR / "memory.txt"
USER_MEMORY_PATH = BASE_DIR / "user.txt"
DB_PATH = BASE_DIR / "sessions.db"
DEFAULT_SESSION_ID = "default"

DEFAULT_CHILD_SAFE_TOOLS = {
    "help",
    "history",
    "recall",
    "read",
    "search",
    "summarize",
    "memories",
    "user_memories",
    "remember",
    "remember_user",
    "tree",
    "run_tests",
}


@dataclass(frozen=True)
class PermissionConfig:
    allow_outside_project_reads: bool = True
    allow_outside_project_writes: bool = True
    allow_sensitive_reads: bool = True
    allow_sensitive_writes: bool = True
    allow_dangerous_terminal: bool = True


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on", "allow", "enabled"}:
        return True
    if value in {"0", "false", "no", "off", "deny", "disabled"}:
        return False
    return default


def permission_config_from_env() -> PermissionConfig:
    profile = os.getenv("SIMPLE_HERMES_PERMISSION_PROFILE", "all").strip().lower() or "all"
    restricted = profile in {"restricted", "safe", "project-only"}
    defaults = PermissionConfig(
        allow_outside_project_reads=not restricted,
        allow_outside_project_writes=not restricted,
        allow_sensitive_reads=not restricted,
        allow_sensitive_writes=not restricted,
        allow_dangerous_terminal=not restricted,
    )
    return PermissionConfig(
        allow_outside_project_reads=_env_flag(
            "SIMPLE_HERMES_ALLOW_OUTSIDE_PROJECT_READS", defaults.allow_outside_project_reads
        ),
        allow_outside_project_writes=_env_flag(
            "SIMPLE_HERMES_ALLOW_OUTSIDE_PROJECT_WRITES", defaults.allow_outside_project_writes
        ),
        allow_sensitive_reads=_env_flag(
            "SIMPLE_HERMES_ALLOW_SENSITIVE_READS", defaults.allow_sensitive_reads
        ),
        allow_sensitive_writes=_env_flag(
            "SIMPLE_HERMES_ALLOW_SENSITIVE_WRITES", defaults.allow_sensitive_writes
        ),
        allow_dangerous_terminal=_env_flag(
            "SIMPLE_HERMES_ALLOW_DANGEROUS_TERMINAL", defaults.allow_dangerous_terminal
        ),
    )


def allowed_tools_from_env(var_name: str) -> set[str] | None:
    raw = os.getenv(var_name, "").strip()
    if not raw or raw.lower() in {"*", "all"}:
        return None
    if raw.lower() in {"safe", "child-safe"}:
        return set(DEFAULT_CHILD_SAFE_TOOLS)
    return {part.strip() for part in raw.split(",") if part.strip()}
