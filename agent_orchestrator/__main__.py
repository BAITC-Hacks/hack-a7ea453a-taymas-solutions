"""Optional CLI: read local outputs and print a browser-safe answer to stdout."""

import argparse
import json
import sys

from .brief import empty_answer
from .contracts import Request, RequestError, for_browser
from .orchestrator import GraphBackend, run


def main(argv=None):
    parser = argparse.ArgumentParser(description="AML Investigation Copilot — локальные факты и гипотезы")
    parser.add_argument("--out", default="out", help="Готовые выгрузки основного пайплайна")
    parser.add_argument("--question", required=True)
    parser.add_argument("--gid", action="append", default=[], help="Выбранный gid; можно повторить до пяти раз")
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--nvidia", action="store_true", help="Явно разрешить NVIDIA для этого вопроса")
    args = parser.parse_args(argv)
    try:
        request = Request(args.question, args.gid, args.depth, args.nvidia)
    except RequestError as exc:
        result = empty_answer("", code="invalid_request", message=str(exc))
    else:
        try:
            from agent_tools import GraphStore, GraphTools

            backend = GraphBackend(GraphTools(GraphStore.from_dir(args.out)))
        except ImportError:
            result = empty_answer(request.question, code="tools_unavailable", message="Сначала подключите PAN-45 graph tools.")
        except Exception:
            result = empty_answer(request.question, code="outputs_unavailable",
                                  message="Не удалось прочитать выгрузки. Сначала выполните основной пайплайн.")
        else:
            result = run(request, backend)
    print(json.dumps(for_browser(result), ensure_ascii=False, allow_nan=False, indent=2))
    return 2 if result["status"] == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
