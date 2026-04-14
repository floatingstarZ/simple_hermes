from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    results_dir = project_root / "test_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = results_dir / f"unittest-{stamp}.log"
    summary_path = results_dir / f"unittest-{stamp}.json"
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    completed = subprocess.run(
        command,
        cwd=project_root,
        text=True,
        capture_output=True,
    )
    combined_output = completed.stdout
    if completed.stderr:
        combined_output += ("\n" if combined_output else "") + completed.stderr
    log_path.write_text(combined_output, encoding="utf-8")
    summary = {
        "command": command,
        "cwd": str(project_root),
        "returncode": completed.returncode,
        "log_path": str(log_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved unittest log: {log_path}")
    print(f"Saved unittest summary: {summary_path}")
    print(combined_output)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
