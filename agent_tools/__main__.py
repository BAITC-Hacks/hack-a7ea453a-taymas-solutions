"""Ручной вызов инструмента из консоли — для проверки и демо.

    python -m agent_tools get_node '{"gid": 100000003684369100}'
    python -m agent_tools find_common_collectors '{"gids": ["100000003684369100", "100000008603629100"]}'
    python -m agent_tools --list
"""

import argparse
import json
import sys

from . import GraphStore, GraphTools, OutputsNotFoundError, call_tool, tool_specs


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Read-only инструменты графа для AML-агента")
    ap.add_argument("tool", nargs="?", help="имя инструмента")
    ap.add_argument("args", nargs="?", default="{}", help="аргументы в JSON")
    ap.add_argument("--out", default="out", help="папка с выгрузками пайплайна")
    ap.add_argument("--list", action="store_true", help="показать инструменты и схемы параметров")
    a = ap.parse_args(argv)

    if a.list or not a.tool:
        print(json.dumps(tool_specs(), ensure_ascii=False, indent=2))
        return
    try:
        tools = GraphTools(GraphStore.from_dir(a.out))
    except OutputsNotFoundError as exc:
        print(f"ОШИБКА: {exc.message}", file=sys.stderr)
        sys.exit(2)
    try:
        args = json.loads(a.args)
    except json.JSONDecodeError as exc:
        print(f"ОШИБКА: аргументы не JSON: {exc}", file=sys.stderr)
        sys.exit(2)
    response = call_tool(tools, a.tool, args)
    print(json.dumps(response, ensure_ascii=False, indent=2))
    sys.exit(0 if response["ok"] else 1)


if __name__ == "__main__":
    main()
