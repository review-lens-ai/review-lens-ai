"""The tool menu: names, descriptions, argument schemas, and one way to run them.

The loop only calls specs() to show the model the menu and run() to execute a
choice. It never sees SQL. An unknown tool or bad arguments come back as a
ToolResult with is_error set (see types.py). The one exception is ToolScopeError
(guard.py): a review from outside the active target is a bug, not the model's
mistake, so it is raised and must stop the question.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from app.services.qa.tools.types import ToolResult


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments: type[BaseModel]
    handler: Callable[[BaseModel], ToolResult]


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def specs(self) -> list[dict]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.arguments.model_json_schema(),
            }
            for tool in self._tools.values()
        ]

    def run(self, name: str, arguments: dict) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            known = ", ".join(sorted(self._tools))
            return _error(f"Unknown tool '{name}'. Available tools: {known}.")
        try:
            parsed = tool.arguments.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
                for e in exc.errors()
            )
            return _error(f"Bad arguments for '{name}': {problems}. Try again.")
        return tool.handler(parsed)


def _error(text: str) -> ToolResult:
    return ToolResult(text=text, is_error=True)
