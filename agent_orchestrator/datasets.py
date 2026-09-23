"""Local dataset lifecycle: bounded uploads, one calculation, atomic snapshots.

Only server-generated IDs reach filesystem paths. A snapshot becomes visible
after the shared CLI pipeline, its output validators and GraphStore all succeed.
"""

import json
import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from email import policy
from email.parser import BytesParser
from numbers import Integral, Real
from pathlib import Path

from money_graph.io import InputSchemaError, load, validate_inputs
from money_graph.outputs import OUTPUT_FILES, write_outputs
from money_graph.pipeline import run as run_pipeline
from money_graph.report import build_report, write_report

from .orchestrator import GraphBackend

MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
INPUT_FILES = {name: f"{name}.parquet" for name in ("nodes", "edges", "transactions")}
DATASET_ID = re.compile(r"^[a-f0-9]{32}$")


class UploadError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def multipart_files(content_type: str, raw: bytes) -> dict[str, bytes]:
    """Parse a bounded browser FormData body; no cgi dependency (Python 3.13)."""
    if len(raw) > MAX_UPLOAD_BYTES:
        raise UploadError("Размер загрузки превышает 32 МиБ.", 413)
    try:
        header = content_type.encode("ascii")
    except UnicodeEncodeError:
        raise UploadError("Некорректный multipart-запрос.") from None
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + header + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw)
    boundary = message.get_boundary()
    if (message.get_content_type() != "multipart/form-data" or not boundary
            or len(boundary) > 200 or not message.is_multipart() or message.defects):
        raise UploadError("Ожидаются три файла в multipart/form-data.", 415)
    files = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if (part.is_multipart() or part.defects or part.get_content_disposition() != "form-data"
                or len(part.get_all("Content-Disposition", [])) != 1
                or part.get("Content-Transfer-Encoding") is not None):
            raise UploadError("Некорректная часть multipart-запроса.")
        if name not in INPUT_FILES or part.get_filename() != INPUT_FILES[name]:
            raise UploadError("Нужны файлы nodes.parquet, edges.parquet и transactions.parquet с соответствующими именами полей.")
        if name in files:
            raise UploadError(f"Файл {INPUT_FILES[name]} передан повторно.")
        data = part.get_payload(decode=True)
        if not data:
            raise UploadError(f"Файл {INPUT_FILES[name]} пуст.")
        if len(data) > MAX_FILE_BYTES:
            raise UploadError(f"Файл {INPUT_FILES[name]} превышает 10 МиБ.", 413)
        files[name] = data
    missing = [INPUT_FILES[name] for name in INPUT_FILES if name not in files]
    if missing:
        raise UploadError("Не хватает файлов: " + ", ".join(missing) + ".")
    return files


def backend_from_dir(out_dir: Path) -> GraphBackend:
    from agent_tools import GraphStore, GraphTools

    return GraphBackend(GraphTools(GraphStore.from_dir(out_dir)))


def validate_gid_bounds(frames):
    """Check raw IDs before pandas int64 coercion can wrap unsigned values.

    Use exact integers/decimals, not float comparisons: int64's upper bound
    rounds to 2**63 in float64. Signed IDs, including zero, keep the CLI domain.
    The shared validator still handles missing columns and non-integer values.
    """
    for name, frame, columns in zip(("edges", "nodes", "transactions"), frames,
                                    (("src", "dst"), ("gid",), ("src", "dst"))):
        for column in columns:
            if column not in frame:
                continue
            for value in frame[column].dropna():
                try:
                    if isinstance(value, Integral):
                        numeric = Decimal(int(value))
                    elif isinstance(value, Real):
                        numeric = Decimal.from_float(float(value))
                    else:
                        numeric = Decimal(str(value))
                except (InvalidOperation, ValueError, TypeError):
                    continue  # Report malformed IDs through validate_inputs.
                if not numeric.is_finite() or not -(2**63) <= numeric <= 2**63 - 1:
                    raise UploadError(f"{name}.parquet: поле {column} содержит идентификатор вне диапазона int64 "
                                      "(-9223372036854775808…9223372036854775807).")
                # pandas may route decimal/exponent strings through float and
                # silently round a valid 18-digit identifier before int64 cast.
                if (not isinstance(value, (Integral, Real, str))
                        or (isinstance(value, str) and not re.fullmatch(r"[+-]?\d+", value.strip()))
                        or (isinstance(value, Real) and not isinstance(value, Integral)
                            and abs(numeric) > 2**53 - 1)):
                    raise UploadError(f"{name}.parquet: поле {column} требует точный целый int64; "
                                      "используйте целочисленный тип или строку из цифр без дробной части и экспоненты.")


@dataclass(frozen=True)
class Snapshot:
    dataset_id: str | None
    backend: GraphBackend
    out_dir: Path | None = None


class DatasetManager:
    def __init__(self, state_dir: Path, legacy_out: Path | None = None,
                 backend: GraphBackend | None = None):
        self.root = Path(state_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.datasets = self.root / "datasets"
        self.datasets.mkdir(exist_ok=True)
        self._lock = threading.RLock()
        self._busy = False
        self._jobs = {}
        self._latest_job = None
        self._starts = {}
        self._snapshot = Snapshot(None, backend) if backend is not None else None
        self._published = set()
        manifest = self.root / "active.json"
        if manifest.exists():
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            dataset_id = saved["dataset_id"]
            published = saved["published"]
            if (not isinstance(published, list)
                    or any(not isinstance(value, str) or not DATASET_ID.fullmatch(value) for value in published)):
                raise ValueError("Invalid published dataset manifest")
            self._published = set(published)
            if not isinstance(dataset_id, str) or dataset_id not in self._published:
                raise ValueError("Invalid active dataset manifest")
            out_dir = self.datasets / dataset_id / "out"
            self._snapshot = Snapshot(dataset_id, backend_from_dir(out_dir), out_dir)
        elif legacy_out is not None and all((Path(legacy_out) / n).is_file() for n in OUTPUT_FILES):
            # Copy once; neither later CLI writes nor browser jobs can mutate it.
            dataset_id = uuid.uuid4().hex
            out_dir = self.datasets / dataset_id / "out"
            files = {n: (Path(legacy_out) / n).read_bytes() for n in OUTPUT_FILES}
            write_outputs(files, out_dir)
            report = Path(legacy_out) / "run_report.json"
            if report.is_file():
                (out_dir / report.name).write_bytes(report.read_bytes())
            self._publish(Snapshot(dataset_id, backend_from_dir(out_dir), out_dir))

    def snapshot(self) -> Snapshot | None:
        with self._lock:
            return self._snapshot

    def active(self) -> dict:
        with self._lock:
            dataset_id = self._snapshot.dataset_id if self._snapshot else None
            return {"dataset_id": dataset_id,
                    "files_base": f"/api/datasets/{dataset_id}/files" if dataset_id else None,
                    "job": self.job(self._latest_job) if self._latest_job else None}

    def reserve(self) -> str:
        """Reserve before reading bytes so concurrent uploads cannot race."""
        with self._lock:
            if self._busy:
                raise UploadError("Анализ уже выполняется. Дождитесь завершения.", 409)
            self._busy = True
            job_id = uuid.uuid4().hex
            self._latest_job = job_id
            self._jobs[job_id] = {"job_id": job_id, "state": "validating", "dataset_id": None,
                                  "error": None, "elapsed_seconds": 0.0}
            return job_id

    def job(self, job_id: str) -> dict | None:
        with self._lock:
            if job_id not in self._jobs:
                return None
            result = dict(self._jobs[job_id])
            if result["state"] in {"validating", "running"} and job_id in self._starts:
                result["elapsed_seconds"] = round(time.perf_counter() - self._starts[job_id], 3)
            return result

    def fail(self, job_id: str, message: str):
        with self._lock:
            self._finish(job_id, "error", error=message)

    def _finish(self, job_id: str, state: str, **fields):
        start = self._starts.pop(job_id, None)
        elapsed = round(time.perf_counter() - start, 3) if start is not None else 0.0
        self._jobs[job_id].update(state=state, elapsed_seconds=elapsed, **fields)
        self._busy = False

    def submit(self, job_id: str, files: dict[str, bytes]):
        with self._lock:
            self._starts[job_id] = time.perf_counter()
        thread = threading.Thread(target=self._calculate, args=(job_id, files), daemon=True,
                                  name=f"dataset-{job_id[:8]}")
        thread.start()

    def _calculate(self, job_id: str, files: dict[str, bytes]):
        data_dir = self.datasets / job_id / "data"
        out_dir = self.datasets / job_id / "out"
        try:
            data_dir.mkdir(parents=True)
            for name, filename in INPUT_FILES.items():
                (data_dir / filename).write_bytes(files[name])
            try:
                frames = load(data_dir)
            except Exception:
                # Keep the shared loader on the normal path; on a read error
                # identify the input without exposing parser internals or paths.
                import pandas as pd

                for filename in INPUT_FILES.values():
                    try:
                        pd.read_parquet(data_dir / filename)
                    except Exception:
                        raise UploadError(f"Не удалось прочитать {filename}. Выберите исправный Parquet-файл.") from None
                raise UploadError("Не удалось прочитать Parquet. Проверьте все три файла.") from None
            validate_gid_bounds(frames)
            edges, nodes, tx, _ = validate_inputs(*frames)
            # The shared validator checks signs; also reject infinities at the
            # upload boundary before numeric graph algorithms consume them.
            if not all(math.isfinite(value) for df in (edges, tx) for value in df.sum_kzt):
                raise UploadError("sum_kzt должен содержать только конечные числа.")
            if nodes.empty:
                raise UploadError("nodes.parquet не содержит узлов.")
            with self._lock:
                self._jobs[job_id]["state"] = "running"
            result = run_pipeline(data_dir)
            write_outputs(result.files, out_dir)
            write_report(build_report(result, data_dir, out_dir, None), out_dir)
            backend = backend_from_dir(out_dir)
            with self._lock:
                if time.perf_counter() - self._starts[job_id] > 300:
                    raise UploadError("Анализ превысил лимит 5 минут. Предыдущий набор данных сохранён.")
                self._publish(Snapshot(job_id, backend, out_dir))
                self._finish(job_id, "ready", dataset_id=job_id)
        except (InputSchemaError, UploadError) as exc:
            self.fail(job_id, str(exc)[:2000])
        except Exception:
            self.fail(job_id, "Не удалось завершить анализ. Проверьте набор данных и свободное место. Предыдущий набор сохранён.")

    def _publish(self, snapshot: Snapshot):
        """Caller holds the lock: version and backend change together."""
        manifest = json.dumps({"dataset_id": snapshot.dataset_id,
                               "published": sorted(self._published | {snapshot.dataset_id})})
        temporary = self.root / "active.next.json"
        temporary.write_text(manifest, encoding="utf-8")
        os.replace(temporary, self.root / "active.json")
        self._published.add(snapshot.dataset_id)
        self._snapshot = snapshot

    def output_file(self, dataset_id: str, filename: str) -> Path | None:
        with self._lock:
            if dataset_id not in self._published or filename not in OUTPUT_FILES:
                return None
            return self.datasets / dataset_id / "out" / filename
