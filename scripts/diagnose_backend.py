from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Simple Hermes backend connectivity without printing secrets.")
    parser.add_argument("--message", default="Return strict JSON: {\"kind\":\"text\",\"text\":\"ping ok\"}")
    parser.add_argument("--traceback", action="store_true")
    parser.add_argument("--agent", action="store_true", help="Diagnose through SimpleAgent.plan() instead of calling the backend directly.")
    parser.add_argument("--run", action="store_true", help="Diagnose through SimpleAgent.run().")
    parser.add_argument("--project-root", default=".", help="Project root for --agent.")
    parser.add_argument("--max-steps", type=int, default=3, help="Maximum agent steps for --run diagnostics.")
    args = parser.parse_args()

    sys.path.insert(0, str(_repo_root()))
    from simple_hermes.agent.backend import backend_from_env

    if args.agent or args.run:
        from simple_hermes.agent import SimpleAgent

        session_id = os.getenv("SIMPLE_HERMES_SESSION_ID", "default")
        agent = SimpleAgent(
            project_root=Path(args.project_root).expanduser().resolve(),
            session_id=session_id,
            max_steps=max(1, args.max_steps),
        )
        print(f"agent_project_root={agent.project_root}")
        print(f"agent_session_id={agent.session_id}")
        print(f"tools_chars={len(agent.tools.help_text())}")
        print(f"memory_chars={len(agent._backend_memory_block())}")
        print(f"history_chars={len(agent._backend_history_text(limit=12))}")
        try:
            if args.run:
                result = agent.run(args.message)
                print(f"run_steps={result.steps}")
                print(f"run_tool={result.tool_used}")
                print(f"run_text={result.final_response[:800]}")
                print(f"trace_kinds={[item.kind for item in result.trace]}")
                return 0
            decision = agent.plan(args.message)
        except Exception as exc:
            print(f"agent_plan_error_type={type(exc).__module__}.{type(exc).__name__}")
            print(f"agent_plan_error={str(exc)[:1200]}")
            cause = getattr(exc, "__cause__", None)
            context = getattr(exc, "__context__", None)
            if cause is not None:
                print(f"cause_type={type(cause).__module__}.{type(cause).__name__}")
                print(f"cause={str(cause)[:1200]}")
            if context is not None and context is not cause:
                print(f"context_type={type(context).__module__}.{type(context).__name__}")
                print(f"context={str(context)[:1200]}")
            if args.traceback:
                traceback.print_exception(type(exc), exc, exc.__traceback__, limit=8)
            return 1
        print(f"decision_kind={decision.kind}")
        print(f"decision_text={decision.text[:500]}")
        if decision.tool_call is not None:
            print(f"decision_tool={decision.tool_call.name}")
            print(f"decision_arg={decision.tool_call.argument[:500]}")
        return 0

    try:
        backend = backend_from_env()
    except Exception as exc:
        print(f"backend_create_error_type={type(exc).__module__}.{type(exc).__name__}")
        print(f"backend_create_error={str(exc)[:1000]}")
        if args.traceback:
            traceback.print_exception(type(exc), exc, exc.__traceback__, limit=8)
        return 2

    if backend is None:
        print("backend=None")
        return 0

    print(f"backend={backend.__class__.__name__}")
    print(f"model={getattr(backend, 'model', '-')}")
    print(f"backend_retries={os.getenv('SIMPLE_HERMES_BACKEND_RETRIES', '(default)')}")
    try:
        decision = backend.plan(
            message=args.message,
            memory_block="",
            history_text="",
            tools_text="No tools available. Return text only.",
        )
    except Exception as exc:
        print(f"plan_error_type={type(exc).__module__}.{type(exc).__name__}")
        print(f"plan_error={str(exc)[:1200]}")
        cause = getattr(exc, "__cause__", None)
        context = getattr(exc, "__context__", None)
        if cause is not None:
            print(f"cause_type={type(cause).__module__}.{type(cause).__name__}")
            print(f"cause={str(cause)[:1200]}")
        if context is not None and context is not cause:
            print(f"context_type={type(context).__module__}.{type(context).__name__}")
            print(f"context={str(context)[:1200]}")
        if args.traceback:
            traceback.print_exception(type(exc), exc, exc.__traceback__, limit=8)
        return 1

    print(f"decision_kind={decision.kind}")
    print(f"decision_text={decision.text[:500]}")
    if decision.tool_call is not None:
        print(f"decision_tool={decision.tool_call.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
