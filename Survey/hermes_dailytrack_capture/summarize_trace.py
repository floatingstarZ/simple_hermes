#!/usr/bin/env python3
"""Build a compact JSON summary for a captured Hermes trace run."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
RUNS_DIR = ROOT_DIR / "runs"
JSONL_NAME = "openai_raw_calls.jsonl"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def latest_run() -> Path:
    candidates = [path for path in RUNS_DIR.iterdir() if (path / JSONL_NAME).exists()]
    if not candidates:
        raise SystemExit(f"No traces found under {RUNS_DIR}")
    return max(candidates, key=lambda path: (path / JSONL_NAME).stat().st_mtime)


def input_count(value: Any) -> int | None:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, str):
        return 1
    return None


def response_id(response: Any) -> str | None:
    if isinstance(response, dict):
        value = response.get("id")
        if isinstance(value, str):
            return value
    return None


def build_summary(run_path: Path) -> dict[str, Any]:
    calls = load_jsonl(run_path / JSONL_NAME)
    api_counts = Counter(str(call.get("api")) for call in calls)
    model_counts = Counter(
        str((call.get("request_kwargs") or {}).get("model"))
        for call in calls
        if (call.get("request_kwargs") or {}).get("model")
    )
    compact_calls = []
    for call in calls:
        request = call.get("request_kwargs") or {}
        stream_events = call.get("stream_events") or []
        compact_calls.append(
            {
                "call_index": call.get("call_index"),
                "api": call.get("api"),
                "started_at": call.get("started_at"),
                "finished_at": call.get("finished_at"),
                "pid": call.get("pid"),
                "thread": call.get("thread"),
                "model": request.get("model"),
                "input_items": input_count(request.get("input")),
                "tool_count": len(request.get("tools") or []) if isinstance(request.get("tools"), list) else None,
                "stream_event_count": len(stream_events),
                "response_id": response_id(call.get("response")),
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_path.name,
        "run_path": str(run_path),
        "trace_jsonl": str(run_path / JSONL_NAME),
        "call_count": len(calls),
        "api_counts": dict(api_counts),
        "model_counts": dict(model_counts),
        "first_started_at": calls[0].get("started_at") if calls else None,
        "last_finished_at": calls[-1].get("finished_at") if calls else None,
        "calls": compact_calls,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize a Hermes OpenAI raw trace JSONL.")
    parser.add_argument("run", nargs="?", help="Run id under runs/. Defaults to the latest run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_path = (RUNS_DIR / args.run).resolve() if args.run else latest_run()
    if not (run_path / JSONL_NAME).exists():
        raise SystemExit(f"Missing {JSONL_NAME}: {run_path}")
    summary = build_summary(run_path)
    out_path = run_path / "trace_summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
