from __future__ import annotations

import argparse
import json
import os
import shutil
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_PATH = REPO_ROOT / "benchmarks" / "code_agent_tasks.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "benchmark_runs"


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": round(self.duration_seconds, 3),
        }


def run_command(command: list[str], cwd: Path, env: dict[str, str] | None = None, timeout: int = 180) -> CommandResult:
    started = time.time()
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=time.time() - started,
    )


def load_tasks(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Task file must contain a JSON list.")
    return data


def copy_template(template: Path, work_root: Path, task_id: str) -> Path:
    target = work_root / task_id
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(template, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return target


def agent_env(task_dir: Path, session_id: str) -> dict[str, str]:
    env = os.environ.copy()
    env["SIMPLE_HERMES_PROJECT_ROOT"] = str(task_dir)
    env["SIMPLE_HERMES_SESSION_ID"] = session_id
    env.setdefault("SIMPLE_HERMES_BACKEND", "hermes-runtime")
    env.setdefault("SIMPLE_HERMES_HERMES_ROOT", str(REPO_ROOT.parent / "hermes-agent"))
    return env


def run_agent(task: dict[str, Any], task_dir: Path, trace_path: Path, timeout: int) -> CommandResult:
    prompt = str(task["prompt"])
    session_id = f"bench-{task['id']}-{int(time.time())}"
    env = agent_env(task_dir, session_id)
    input_text = f"{prompt}\n/trace\nexit\n"
    command = ["simple_hermes_codex"]
    started = time.time()
    with trace_path.open("w", encoding="utf-8") as trace_file:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            input=input_text,
            text=True,
            stdout=trace_file,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    return CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=f"trace saved to {trace_path}",
        stderr="",
        duration_seconds=time.time() - started,
    )


def run_one_task(task: dict[str, Any], output_dir: Path, run_agent_flag: bool, timeout: int) -> dict[str, Any]:
    template = REPO_ROOT / str(task["template"])
    if not template.exists():
        raise FileNotFoundError(f"Template not found: {template}")
    work_root = output_dir / "workspaces"
    trace_dir = output_dir / "traces"
    work_root.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)
    task_dir = copy_template(template, work_root, str(task["id"]))

    test_command = shlex.split(str(task["test_command"]))
    initial_test = run_command(test_command, cwd=task_dir, timeout=timeout)

    agent_result = None
    if run_agent_flag:
        trace_path = trace_dir / f"{task['id']}.txt"
        agent_result = run_agent(task, task_dir, trace_path, timeout=timeout)

    final_test = run_command(test_command, cwd=task_dir, timeout=timeout)
    ready = initial_test.returncode != 0
    passed = ready and final_test.returncode == 0

    return {
        "id": task["id"],
        "name": task["name"],
        "category": task["category"],
        "template": task["template"],
        "workspace": str(task_dir),
        "expected_initial_failure": ready,
        "passed": passed,
        "initial_test": initial_test.as_dict(),
        "agent": None if agent_result is None else agent_result.as_dict(),
        "final_test": final_test.as_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Simple Hermes code-agent benchmark fixtures.")
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--run-agent", action="store_true", help="Actually run simple_hermes_codex for each task.")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    tasks = load_tasks(args.tasks)
    if args.task_id:
        wanted = set(args.task_id)
        tasks = [task for task in tasks if task.get("id") in wanted]
    if not tasks:
        raise SystemExit("No benchmark tasks selected.")

    run_id = time.strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    results = [run_one_task(task, output_dir, args.run_agent, args.timeout) for task in tasks]
    summary = {
        "run_id": run_id,
        "run_agent": args.run_agent,
        "total": len(results),
        "ready": sum(1 for result in results if result["expected_initial_failure"]),
        "passed": sum(1 for result in results if result["passed"]),
        "results": results,
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "summary": str(summary_path),
        "total": summary["total"],
        "ready": summary["ready"],
        "passed": summary["passed"],
        "run_agent": args.run_agent,
    }, ensure_ascii=False, indent=2))
    return 0 if (not args.run_agent or summary["passed"] == summary["total"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
