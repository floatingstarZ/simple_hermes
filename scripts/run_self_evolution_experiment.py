from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "self_evolution_runs"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simple_hermes.state.memory import MemoryStore
from simple_hermes.state.session import SessionStore
from simple_hermes.tools.builtin import BuiltInTools


def write_demo_project(project_root: Path) -> None:
    (project_root / "tests").mkdir(parents=True, exist_ok=True)
    (project_root / "README.md").write_text(
        "# Self Evolution Demo\n\nA tiny project used to validate Simple Hermes skill candidates.\n",
        encoding="utf-8",
    )
    (project_root / "calculator.py").write_text(
        "def total(items):\n"
        "    return sum(items)\n",
        encoding="utf-8",
    )
    (project_root / "tests" / "test_calculator.py").write_text(
        "import unittest\n\n"
        "from calculator import total\n\n\n"
        "class CalculatorTests(unittest.TestCase):\n"
        "    def test_total(self):\n"
        "        self.assertEqual(total([2, 3, 5]), 10)\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8",
    )


def parse_candidate_id(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("Created self-evolution candidate "):
            return line.split()[3].rstrip(":")
    return ""


def run_experiment(output_dir: Path, promote: bool = False) -> dict[str, Any]:
    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir = output_dir / run_id
    project_root = run_dir / "workspace"
    run_dir.mkdir(parents=True, exist_ok=False)
    write_demo_project(project_root)

    memory = MemoryStore(memory_path=run_dir / "memory.txt", user_path=run_dir / "user_memory.txt")
    sessions = SessionStore(path=run_dir / "sessions.db")
    try:
        tools = BuiltInTools(memory, sessions, project_root, session_id=f"self-evolution-exp-{run_id}")
        tools.skill_candidates_dir = run_dir / "skill_candidates"
        tools.skills_dir = run_dir / "stable_skills"

        seed_outputs = [
            tools.experience(
                "record status=failed failure_type=test_failure target=run_tests "
                "goal='recover failing test' ::: AssertionError before patching calculator total"
            ),
            tools.experience(
                "record status=failed failure_type=test_failure target=run_tests "
                "goal='recover failing test' ::: ValueError in parser after changing input shape"
            ),
        ]
        before = tools.self_evolve("status")
        evolution = tools.self_evolve(
            "run name=test-failure-recovery failure_type=test_failure min_count=2 "
            "command='discover -s tests -v'"
        )
        candidate_id = parse_candidate_id(evolution)
        candidate_view = tools.skills(f"view-candidate {candidate_id}") if candidate_id else ""
        after = tools.self_evolve("status")
        promotion = tools.skills(f"promote {candidate_id}") if promote and candidate_id else ""

        summary = {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "workspace": str(project_root),
            "experience_log": str(tools._experience_log_path()),
            "skill_candidates_dir": str(tools.skill_candidates_dir),
            "stable_skills_dir": str(tools.skills_dir),
            "seed_outputs": seed_outputs,
            "before": json.loads(before),
            "evolution_output": evolution,
            "candidate_id": candidate_id,
            "candidate_preview": candidate_view[:1200],
            "after": json.loads(after),
            "promotion_output": promotion,
        }
        (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    finally:
        sessions.conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an offline Simple Hermes self-evolution experiment.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--promote", action="store_true", help="Promote the validated candidate into the isolated experiment skill dir.")
    args = parser.parse_args()

    summary = run_experiment(args.output_dir, promote=args.promote)
    print(
        json.dumps(
            {
                "summary": str(Path(summary["run_dir"]) / "summary.json"),
                "candidate_id": summary["candidate_id"],
                "experience_cards": summary["after"]["experience_cards"],
                "validated_candidates": summary["after"]["validated_candidates"],
                "promoted": bool(summary["promotion_output"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
