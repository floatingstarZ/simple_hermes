from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

ToolHandler = Callable[[str], str]


@dataclass
class Tool:
    name: str
    description: str
    handler: ToolHandler


class ToolRegistry:
    def __init__(self, allowed_tools: set[str] | None = None) -> None:
        self._tools: Dict[str, Tool] = {}
        self.allowed_tools = allowed_tools

    def register(self, name: str, description: str, handler: ToolHandler) -> None:
        self._tools[name] = Tool(name=name, description=description, handler=handler)

    def run(self, name: str, arg: str) -> str:
        if name not in self._tools:
            return f"Unknown tool: {name}"
        if self.allowed_tools is not None and name not in self.allowed_tools:
            return f"Tool not allowed in this agent: {name}"
        return self._tools[name].handler(arg)

    def help_text(self) -> str:
        lines = ["Available tools:"]
        for tool in self._tools.values():
            if self.allowed_tools is not None and tool.name not in self.allowed_tools:
                continue
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines)
