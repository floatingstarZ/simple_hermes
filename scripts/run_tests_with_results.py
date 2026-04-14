from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import subprocess
import sys
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def parse_unittest_output(output: str) -> list[dict[str, str]]:
    tests: list[dict[str, str]] = []
    pattern = re.compile(r"^(?P<name>\S+)\s+\((?P<class>[^)]+)\)\s+\.\.\.\s+(?P<status>ok|FAIL|ERROR|skipped .+)$")
    for line in output.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        data = match.groupdict()
        tests.append(
            {
                "name": data["name"],
                "class": data["class"],
                "status": data["status"],
            }
        )
    return tests


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def parse_trace_steps(output: str) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    pattern = re.compile(r"step (?P<step>\d+) · (?P<kind>\S+)(?: \[(?P<tool>[^\]]+)\])?\s*(?P<detail>.*)")
    for line in strip_ansi(output).splitlines():
        match = pattern.search(line)
        if not match:
            continue
        data = match.groupdict()
        steps.append(
            {
                "step": data["step"],
                "kind": data["kind"],
                "tool": data.get("tool") or "",
                "detail": data.get("detail") or "",
            }
        )
    return steps


def write_html_report(path: Path, *, command: list[str], cwd: Path, returncode: int, tests: list[dict[str, str]], output: str) -> None:
    passed = sum(1 for test in tests if test["status"] == "ok")
    failed = len(tests) - passed
    rows = []
    for test in tests:
        status = test["status"]
        css = "ok" if status == "ok" else "bad"
        rows.append(
            "<tr>"
            f"<td>{html.escape(status)}</td>"
            f"<td>{html.escape(test['class'])}</td>"
            f"<td>{html.escape(test['name'])}</td>"
            f"<td><span class='{css}'>{html.escape('PASS' if status == 'ok' else 'CHECK')}</span></td>"
            "</tr>"
        )
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Simple Hermes Codex Test Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2937; background: #f8fafc; }}
    h1 {{ margin-bottom: 8px; }}
    .summary {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 18px 0 24px; }}
    .card {{ background: white; border: 1px solid #d1d5db; border-radius: 8px; padding: 14px 16px; min-width: 150px; }}
    .label {{ color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: 0; }}
    .value {{ font-size: 22px; font-weight: 700; margin-top: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #d1d5db; border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid #e5e7eb; text-align: left; font-size: 14px; }}
    th {{ background: #eef2ff; }}
    .ok {{ color: #047857; font-weight: 700; }}
    .bad {{ color: #b91c1c; font-weight: 700; }}
    pre {{ white-space: pre-wrap; background: #111827; color: #f9fafb; padding: 16px; border-radius: 8px; overflow: auto; }}
    code {{ background: #e5e7eb; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Simple Hermes Codex Test Report</h1>
  <p><code>{html.escape(" ".join(command))}</code></p>
  <p>cwd: <code>{html.escape(str(cwd))}</code></p>
  <div class="summary">
    <div class="card"><div class="label">Return code</div><div class="value">{returncode}</div></div>
    <div class="card"><div class="label">Tests</div><div class="value">{len(tests)}</div></div>
    <div class="card"><div class="label">Passed</div><div class="value ok">{passed}</div></div>
    <div class="card"><div class="label">Needs check</div><div class="value {'bad' if failed else 'ok'}">{failed}</div></div>
  </div>
  <h2>Per-test view</h2>
  <table>
    <thead><tr><th>Status</th><th>Class</th><th>Test</th><th>Visual result</th></tr></thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
  <h2>Raw log</h2>
  <pre>{html.escape(output)}</pre>
</body>
</html>
"""
    path.write_text(body, encoding="utf-8")


def write_trace_html_report(path: Path, *, title: str, log_path: Path, output: str) -> None:
    clean_output = strip_ansi(output)
    steps = parse_trace_steps(output)
    rows = []
    for item in steps:
        css = "ok" if item["kind"] == "tool_result" else "pending"
        rows.append(
            "<tr>"
            f"<td>{html.escape(item['step'])}</td>"
            f"<td><span class='{css}'>{html.escape(item['kind'])}</span></td>"
            f"<td>{html.escape(item['tool'])}</td>"
            f"<td>{html.escape(item['detail'])}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='4'>No streamed steps detected in this log.</td></tr>")
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2937; background: #f8fafc; }}
    h1 {{ margin-bottom: 8px; }}
    .summary {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 18px 0 24px; }}
    .card {{ background: white; border: 1px solid #d1d5db; border-radius: 8px; padding: 14px 16px; min-width: 150px; }}
    .label {{ color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: 0; }}
    .value {{ font-size: 22px; font-weight: 700; margin-top: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #d1d5db; border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid #e5e7eb; text-align: left; font-size: 14px; vertical-align: top; }}
    th {{ background: #eef2ff; }}
    .ok {{ color: #047857; font-weight: 700; }}
    .pending {{ color: #0369a1; font-weight: 700; }}
    pre {{ white-space: pre-wrap; background: #111827; color: #f9fafb; padding: 16px; border-radius: 8px; overflow: auto; }}
    code {{ background: #e5e7eb; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p>source log: <code>{html.escape(str(log_path))}</code></p>
  <div class="summary">
    <div class="card"><div class="label">Detected steps</div><div class="value">{len(steps)}</div></div>
    <div class="card"><div class="label">Log lines</div><div class="value">{len(clean_output.splitlines())}</div></div>
  </div>
  <h2>Streamed step view</h2>
  <table>
    <thead><tr><th>Step</th><th>Kind</th><th>Tool</th><th>Detail</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <h2>Raw log</h2>
  <pre>{html.escape(clean_output)}</pre>
</body>
</html>
"""
    path.write_text(body, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Simple Hermes tests and write log/json/html artifacts.")
    parser.add_argument("--render-log", type=Path, help="Render an existing plain-text trace log as HTML.")
    parser.add_argument("--title", default="Simple Hermes Codex Trace Report")
    parser.add_argument("--html", type=Path, help="Output HTML path for --render-log.")
    args = parser.parse_args()
    if args.render_log:
        output = args.render_log.read_text(encoding="utf-8", errors="replace")
        html_path = args.html or args.render_log.with_suffix(".html")
        write_trace_html_report(html_path, title=args.title, log_path=args.render_log, output=output)
        print(f"Saved trace HTML report: {html_path}")
        return 0

    project_root = Path(__file__).resolve().parents[1]
    results_dir = project_root / "test_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = results_dir / f"unittest-{stamp}.log"
    summary_path = results_dir / f"unittest-{stamp}.json"
    html_path = results_dir / f"unittest-{stamp}.html"
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
    tests = parse_unittest_output(combined_output)
    write_html_report(
        html_path,
        command=command,
        cwd=project_root,
        returncode=completed.returncode,
        tests=tests,
        output=combined_output,
    )
    summary = {
        "command": command,
        "cwd": str(project_root),
        "returncode": completed.returncode,
        "log_path": str(log_path),
        "html_path": str(html_path),
        "test_count": len(tests),
        "passed": sum(1 for test in tests if test["status"] == "ok"),
        "tests": tests,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved unittest log: {log_path}")
    print(f"Saved unittest summary: {summary_path}")
    print(f"Saved unittest HTML report: {html_path}")
    print(combined_output)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
