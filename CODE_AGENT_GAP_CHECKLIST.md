# Code Agent Gap Checklist

Working fixtures:

- `fixtures/code_agent_sandbox`
- `fixtures/game_agent_sandbox_template`

The fixture is a nested local git repo and is intentionally kept as a sandbox for end-to-end code-agent tasks. Do not push it.

## Checked References

- Local Hermes project: tool dispatch, provider isolation, execution-loop recovery, and code-execution notes.
- Local Claude Code notes/source: tool permission surfaces, Read/Grep/Glob/Edit/Bash-style workflow, and diff-oriented UI.
- Local Codex notes: sandbox/approval model, skills/plugins, multi-agent and diff/review workflows.

## Current First-Pass Gaps

- Project discovery needs stronger file-location tools than `tree` and `search`.
- Code reading needs line-numbered slices, not only whole-file reads.
- Editing needs robust exact replacement with multiline payloads.
- Verification must not inherit the agent's own LLM backend environment.
- The agent loop must avoid premature success claims before edits/tests.
- Diff inspection should be available as a first-class tool.

## Implemented In This Pass

- `read_lines <path> <start> <end>` for precise code context.
- `glob <pattern>` for project file discovery.
- `project_overview` for project markers, file counts, and likely verification commands.
- `diff [path]` for git diff inspection.
- Prompt guidance that prefers project inspection, exact patch targets, diff inspection, and tests.
- Local `.gitignore` for the main project and nested sandbox.
- A web-game code-agent scenario that requires project discovery, JS editing, diff inspection, and unittest verification.
- Broader code-change intent detection for create/build/add/fix style prompts and common Chinese equivalents.
- Loop guard for repeated successful project-inspection calls such as `project_overview`, `tree`, `glob`, `search`, and `read_lines`.
- Project-scoped default CLI session ids, with `SIMPLE_HERMES_SESSION_ID` override, to avoid cross-project history contaminating code-agent traces.
- Per-run tool-state summaries in planner follow-up messages, so the backend can use earlier project inspection, read, edit, and test results instead of only the last tool result.
- `run_tests <directory>` now maps to `unittest discover -s <directory> -v`, avoiding false-positive `Ran 0 tests` results for common test-directory arguments.

## Next Pass Candidates

- Add a small task/todo state tool for multi-step coding tasks.
- Add a structured `project_overview` helper that reports language, package manager, and likely test commands.
- Add patch validation that reports nearest matching text when the target is missing.
- Add an end-to-end fixture runner that resets `fixtures/code_agent_sandbox` and captures traces automatically.
