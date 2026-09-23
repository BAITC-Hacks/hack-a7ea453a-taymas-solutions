"""Local, optional HTTP bridge for PAN-48. No changes to the CSV pipeline."""

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .brief import empty_answer
from .contracts import MAX_ANSWER_BYTES, Request, RequestError, for_browser, json_text
from .orchestrator import GraphBackend, run

MAX_REQUEST_BYTES = 8192
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def make_server(backend: GraphBackend, port=8765):
    """Bind loopback only. The backend is a startup snapshot of local outputs."""
    slots = threading.BoundedSemaphore(2)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            # Never log a question, gid, request body, credential or exception.
            pass

        def send_json(self, status, payload):
            raw = json_text(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Browser cancelled; graph remains usable.

        def fail(self, status, code, message):
            self.send_json(status, for_browser(empty_answer("", code=code, message=message)))

        def local_request(self):
            try:
                host = urlsplit("http://" + self.headers.get("Host", "")).hostname
                origin = self.headers.get("Origin")
                allowed_origin = not origin or (urlsplit(origin).scheme in {"http", "https"}
                                               and urlsplit(origin).hostname in LOCAL_HOSTS)
                return host in LOCAL_HOSTS and allowed_origin
            except ValueError:
                return False

        def do_GET(self):
            if not self.local_request():
                return self.fail(403, "forbidden", "Разрешены только локальные запросы.")
            if self.path != "/api/copilot/status":
                return self.fail(404, "not_found", "Неизвестный запрос.")
            self.send_json(200, {"ready": True, "nvidia_available": bool(
                os.environ.get("NVIDIA_API_KEY", "").strip() and os.environ.get("NVIDIA_MODEL", "").strip())})

        def do_POST(self):
            if not self.local_request():
                return self.fail(403, "forbidden", "Разрешены только локальные запросы.")
            if self.path != "/api/copilot/answer":
                return self.fail(404, "not_found", "Неизвестный запрос.")
            if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
                return self.fail(415, "invalid_content_type", "Ожидается JSON-запрос.")
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST_BYTES:
                    return self.fail(413, "request_limit", "Вопрос слишком большой.")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("incomplete body")
                request = Request.from_dict(json.loads(raw))
            except (ValueError, TypeError, RequestError, RecursionError):
                return self.fail(400, "invalid_request", "Проверьте вопрос и выбранные gid.")
            except (OSError, TimeoutError):
                return self.fail(408, "request_timeout", "Время передачи вопроса истекло.")
            if not slots.acquire(blocking=False):
                return self.fail(503, "busy", "Помощник занят. Повторите запрос через несколько секунд.")
            try:
                result = for_browser(run(request, backend))
                if len(json_text(result).encode("utf-8")) > MAX_ANSWER_BYTES:
                    return self.fail(502, "response_limit", "Ответ слишком большой. Уточните вопрос.")
                self.send_json(200, result)
            except Exception:
                self.fail(500, "unavailable", "Помощник временно недоступен. Попробуйте ещё раз.")
            finally:
                slots.release()

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Локальный API AML Copilot для React")
    parser.add_argument("--out", default="out", help="Готовые CSV основного пайплайна")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    from agent_tools import GraphStore, GraphTools

    try:
        backend = GraphBackend(GraphTools(GraphStore.from_dir(args.out)))
        server = make_server(backend, args.port)
    except Exception:
        parser.exit(2, "Не удалось запустить Copilot: проверьте локальные выгрузки и свободен ли порт.\n")
    print(f"Copilot: http://127.0.0.1:{server.server_port} (локальный режим доступен без NVIDIA)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
