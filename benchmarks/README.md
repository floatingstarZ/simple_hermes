# Code Agent Benchmarks

These fixtures are small benchmark-style tasks for `simple_hermes_codex`.

Each task starts from a failing test state. A successful code agent run should inspect the project, edit the relevant code, and make the test command pass.

## Tasks

- `coin-catcher-score`: JavaScript game scoring bug.
- `todo-active-items`: Python business-logic filtering bug.

## Dry Run

Validate that every fixture starts from an expected failing-test state:

```bash
python3 scripts/run_code_agent_benchmark.py
```

In dry-run mode, `ready` should equal `total`; `passed` is expected to be 0 because the agent was not run.

## Agent Run

Run `simple_hermes_codex` against every task and save traces/results under `benchmark_runs/`:

```bash
https_proxy=http://127.0.0.1:7890 \
http_proxy=http://127.0.0.1:7890 \
all_proxy=socks5://127.0.0.1:7890 \
HTTPS_PROXY=http://127.0.0.1:7890 \
HTTP_PROXY=http://127.0.0.1:7890 \
ALL_PROXY=socks5://127.0.0.1:7890 \
python3 scripts/run_code_agent_benchmark.py --run-agent
```

The generated `summary.json` records each workspace, initial test result, final test result, and trace path.
