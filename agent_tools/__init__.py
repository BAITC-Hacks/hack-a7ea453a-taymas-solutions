"""Read-only инструменты графа для AML-агента (PAN-45).

    from agent_tools import GraphStore, GraphTools, call_tool

    tools = GraphTools(GraphStore.from_dir("out"))
    tools.get_node(100000003684369100)
    call_tool(tools, "find_common_collectors", {"gids": [...]})   # для оркестратора

Инструменты не используют LLM и сеть, ничего не пишут на диск и отвечают
только фактами из nodes_roles.csv, edge_table.csv, clusters.csv и top_nodes.csv.
"""

from .errors import (InvalidArgumentError, OutputsNotFoundError, ToolError, ToolNotAllowedError,
                     UnknownClusterError, UnknownGidError)
from .schemas import DESCRIPTIONS, PARAMETERS, RESULTS, validate
from .store import GraphStore
from .tools import TOOL_NAMES, GraphTools

ALLOWED_TOOLS = frozenset(TOOL_NAMES)


def tool_specs() -> list[dict]:
    """Описание инструментов в формате OpenAI-совместимого tool calling (NVIDIA API)."""
    return [{"type": "function",
             "function": {"name": name, "description": DESCRIPTIONS[name], "parameters": PARAMETERS[name]}}
            for name in TOOL_NAMES]


def call_tool(tools: GraphTools, name: str, args: dict | None = None) -> dict:
    """Единая точка вызова для агента: allow-list, проверка аргументов по схеме,
    типизированная ошибка вместо исключения.

    Успех:  {"ok": True,  "tool": name, "result": {...}}
    Ошибка: {"ok": False, "tool": name, "error": {"type", "code", "message", ...}}
    """
    try:
        if name not in ALLOWED_TOOLS:
            raise ToolNotAllowedError(name)
        args = {} if args is None else args
        if not isinstance(args, dict):
            raise InvalidArgumentError(f"аргументы должны быть объектом, получено {type(args).__name__}")
        problems = validate(args, PARAMETERS[name], path="args")
        if problems:
            raise InvalidArgumentError("; ".join(problems))
        return {"ok": True, "tool": name, "result": getattr(tools, name)(**args)}
    except ToolError as exc:
        return {"ok": False, "tool": name, "error": exc.to_dict()}


__all__ = ["ALLOWED_TOOLS", "GraphStore", "GraphTools", "InvalidArgumentError", "OutputsNotFoundError",
           "RESULTS", "TOOL_NAMES", "ToolError", "ToolNotAllowedError", "UnknownClusterError",
           "UnknownGidError", "call_tool", "tool_specs", "validate"]
