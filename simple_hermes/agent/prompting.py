from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class PromptContext:
    message: str
    memory_block: str
    history_text: str
    tools_text: str


def build_planner_prompt(ctx: PromptContext) -> str:
    payload = {
        "task": "Decide whether to answer with plain text or call exactly one tool.",
        "output_schema": {
            "kind": "tool_call or text",
            "tool": "tool name if kind=tool_call",
            "argument": "tool argument string if needed",
            "text": "short assistant text or explanation",
        },
        "rules": [
            "Prefer a tool call when a tool clearly matches.",
            "For coding tasks, inspect the project first with project_overview, tree, glob, search, read, or read_lines before editing.",
            "For app/game/file creation requests, choose a reasonable simple project-local implementation when the user has not specified every detail; do not ask for clarification when you can proceed safely.",
            "If the current message is a short follow-up such as a selected version or permission to proceed, use recent_history and the expanded user_message to continue the previous coding task.",
            "Use project-relative paths when calling file tools.",
            "If a file path is missing, use the suggested path or call tree/glob/search/terminal to locate it.",
            "Never repeat the same failed tool call with the same argument.",
            "If the user asks for a code change, do not run tests until a write_file or patch_file tool call has succeeded.",
            "Before patch_file, make sure the exact target text appears in a prior tool result; if a read result is truncated, use read_lines or search to inspect the exact lines first.",
            "When using patch_file from JSON, prefer a single argument string in this exact format: path ::: exact target text ::: replacement text.",
            "After reading the relevant file, move to patch_file/write_file or use read_lines/terminal for specific lines; do not repeatedly read the same file.",
            "After editing code, use diff to inspect the change and run a relevant test command before claiming completion if the user asked to verify or complete a coding task.",
            "If a test command fails, use the failure output to patch the implementation, then rerun tests before giving a final answer.",
            "Use kind=text if no tool is appropriate.",
            "Return valid JSON only.",
        ],
        "memory": ctx.memory_block,
        "recent_history": ctx.history_text,
        "available_tools": ctx.tools_text,
        "user_message": ctx.message,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
