"""Local HTTP API for versioned datasets and the AML Copilot."""

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .brief import empty_answer
from .contracts import MAX_ANSWER_BYTES, Request, RequestError, for_browser, json_text
from .datasets import DatasetManager, MAX_UPLOAD_BYTES, Snapshot, UploadError, multipart_files
from .orchestrator import GraphBackend, run

MAX_REQUEST_BYTES = 8192
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def make_server(backend: GraphBackend | None = None, port=8765, host="127.0.0.1", *,
                manager: DatasetManager | None = None):
    """Loopback by default; Docker explicitly binds its internal network interface."""
    if host not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("Unsupported bind address")
    slots = threading.BoundedSemaphore(2)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            # Never log a question, gid, request body, credential or exception.
            pass

        def send_json(self, status, payload, dataset_version=None):
            raw = json_text(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if dataset_version is not None:
                self.send_header("X-Dataset-Version", dataset_version)
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Browser cancelled; graph remains usable.

        def fail(self, status, code, message):
            self.send_json(status, for_browser(empty_answer("", code=code, message=message)))

        def snapshot(self):
            return manager.snapshot() if manager is not None else (Snapshot(None, backend) if backend else None)

        def dataset_error(self, status, message):
            self.send_json(status, {"error": message})

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
            if self.path == "/api/copilot/status":
                snapshot = self.snapshot()
                return self.send_json(200, {"ready": snapshot is not None,
                    "dataset_id": snapshot.dataset_id if snapshot else None,
                    "nvidia_available": bool(os.environ.get("NVIDIA_API_KEY", "").strip()
                                             and os.environ.get("NVIDIA_MODEL", "").strip())})
            if self.path.startswith("/api/datasets/"):
                if manager is None:
                    return self.dataset_error(503, "Загрузка данных не настроена.")
                if self.path == "/api/datasets/active":
                    return self.send_json(200, manager.active())
                parts = self.path.split("/")
                if len(parts) == 5 and parts[3] == "jobs":
                    job = manager.job(parts[4])
                    return self.send_json(200, job) if job else self.dataset_error(404, "Задание не найдено.")
                if len(parts) == 6 and parts[4] == "files":
                    file = manager.output_file(parts[3], parts[5])
                    if file is None or not file.is_file():
                        return self.dataset_error(404, "Выгрузка не найдена.")
                    raw = file.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Content-Disposition", f'attachment; filename="{file.name}"')
                    self.send_header("Cache-Control", "private, max-age=31536000, immutable")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("X-Dataset-Version", parts[3])
                    self.end_headers()
                    try:
                        self.wfile.write(raw)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                return self.dataset_error(404, "Неизвестный запрос к данным.")
            return self.fail(404, "not_found", "Неизвестный запрос.")

        def upload(self):
            if manager is None:
                return self.dataset_error(503, "Загрузка данных не настроена.")
            job_id = None
            submitted = False
            try:
                if self.headers.get_content_type() != "multipart/form-data" or self.headers.get("Transfer-Encoding"):
                    raise UploadError("Ожидаются три файла в multipart/form-data.", 415)
                if len(self.headers.get_all("Content-Length", [])) != 1:
                    raise UploadError("Укажите размер загрузки в Content-Length.", 411)
                try:
                    length = int(self.headers["Content-Length"])
                except (ValueError, TypeError):
                    raise UploadError("Некорректный размер загрузки.") from None
                if not 0 < length <= MAX_UPLOAD_BYTES:
                    raise UploadError("Размер загрузки должен быть от 1 байта до 32 МиБ.", 413)
                job_id = manager.reserve()
                self.connection.settimeout(60)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise UploadError("Передача файлов прервалась. Повторите загрузку.")
                files = multipart_files(self.headers["Content-Type"], raw)
                manager.submit(job_id, files)
                submitted = True
                self.send_json(202, manager.job(job_id))
            except UploadError as exc:
                if job_id is not None and not submitted:
                    manager.fail(job_id, str(exc))
                self.dataset_error(exc.status, str(exc))
            except (OSError, TimeoutError):
                message = "Не удалось получить файлы. Повторите загрузку."
                if submitted:
                    return  # Browser disconnects cannot cancel an accepted job.
                if job_id is not None:
                    manager.fail(job_id, message)
                self.dataset_error(408, message)
            except Exception:
                message = "Не удалось принять файлы. Предыдущий набор данных сохранён."
                if submitted:
                    return
                if job_id is not None:
                    manager.fail(job_id, message)
                self.dataset_error(500, message)

        def do_POST(self):
            if not self.local_request():
                return self.fail(403, "forbidden", "Разрешены только локальные запросы.")
            if self.path == "/api/datasets/jobs":
                return self.upload()
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
            snapshot = self.snapshot()
            if snapshot is None:
                return self.fail(503, "no_dataset", "Загрузите данные и постройте граф перед вопросом помощнику.")
            expected_version = self.headers.get("X-Dataset-Version")
            if expected_version is not None and expected_version != snapshot.dataset_id:
                return self.fail(409, "dataset_changed", "Набор данных изменился. Обновите граф и повторите вопрос.")
            if not slots.acquire(blocking=False):
                return self.fail(503, "busy", "Помощник занят. Повторите запрос через несколько секунд.")
            try:
                result = for_browser(run(request, snapshot.backend))
                if len(json_text(result).encode("utf-8")) > MAX_ANSWER_BYTES:
                    return self.fail(502, "response_limit", "Ответ слишком большой. Уточните вопрос.")
                self.send_json(200, result, snapshot.dataset_id)
            except Exception:
                self.fail(500, "unavailable", "Помощник временно недоступен. Попробуйте ещё раз.")
            finally:
                slots.release()

    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Локальный API AML Copilot для React")
    parser.add_argument("--out", default="out", help="Готовые CSV основного пайплайна")
    parser.add_argument("--state", default=".money-graph", help="Локальная папка загруженных наборов и версий анализа")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", choices=["127.0.0.1", "0.0.0.0"], default="127.0.0.1",
                        help="0.0.0.0 только для внутренней сети Docker")
    args = parser.parse_args(argv)
    try:
        manager = DatasetManager(Path(args.state), legacy_out=Path(args.out))
        server = make_server(port=args.port, host=args.host, manager=manager)
    except Exception:
        parser.exit(2, "Не удалось запустить API: проверьте папку --state, локальные выгрузки и свободен ли порт.\n")
    print(f"Граф и Copilot: http://{args.host}:{server.server_port} (загрузка через UI, NVIDIA не требуется)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
