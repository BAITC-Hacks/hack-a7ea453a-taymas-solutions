"""Browser upload contract using real Parquet, pipeline and local HTTP server."""

import http.client
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from agent_orchestrator.datasets import DatasetManager, MAX_FILE_BYTES, MAX_UPLOAD_BYTES
from agent_orchestrator.server import make_server
from money_graph.cli import main as cli
from money_graph.outputs import OUTPUT_FILES
from tests._mini import mini_frames

ISOLATED_GID = int(mini_frames()[1].iloc[-1].gid)


def parquet_files(change=None, offset=0):
    edges, nodes, transactions = mini_frames()
    nodes.gid += offset
    for table in (edges, transactions):
        table.src += offset
        table.dst += offset
    frames = {"nodes": nodes, "edges": edges, "transactions": transactions}
    if change:
        change(frames)
    result = {}
    for name, frame in frames.items():
        output = io.BytesIO()
        frame.to_parquet(output, index=False)
        result[name] = output.getvalue()
    return result


def multipart(files, *, names=None, filename=None):
    boundary = "money-graph-test-boundary"
    parts = []
    for name in names or list(files):
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                      f'filename="{filename or name + ".parquet"}"\r\n'
                      "Content-Type: application/octet-stream\r\n\r\n").encode() + files[name] + b"\r\n")
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), {
        "Content-Type": f"multipart/form-data; boundary={boundary}"}


@contextmanager
def running(manager):
    server = make_server(port=0, manager=manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def service(tmp_path):
    manager = DatasetManager(tmp_path / "state")
    with running(manager) as server:
        yield server, manager


def request(server, path, *, body=None, headers=None, method=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        connection.request(method or ("POST" if body is not None else "GET"), path,
                           body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        payload = json.loads(raw) if "application/json" in response.getheader("Content-Type", "") else raw
        return response.status, payload, dict(response.getheaders())
    finally:
        connection.close()


def submit(server, files=None, **kwargs):
    body, headers = multipart(files if files is not None else parquet_files(), **kwargs)
    return request(server, "/api/datasets/jobs", body=body, headers=headers)


def wait_job(server, job):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status, result, _ = request(server, f'/api/datasets/jobs/{job["job_id"]}')
        assert status == 200
        if result["state"] in {"ready", "error"}:
            return result
        time.sleep(0.02)
    pytest.fail("Dataset job did not finish")


def upload(server, files=None):
    status, job, _ = submit(server, files)
    assert status == 202, job
    result = wait_job(server, job)
    assert result["state"] == "ready", result
    assert result["elapsed_seconds"] > 0
    return result["dataset_id"]


def answer(server, version=None):
    headers = {"Content-Type": "application/json"}
    if version is not None:
        headers["X-Dataset-Version"] = version
    return request(server, "/api/copilot/answer", headers=headers,
                   body=json.dumps({"question": "Кого проверить первым и почему?", "use_nvidia": False}))


def test_cold_start_empty_status_and_answer(service):
    server, _ = service
    assert request(server, "/api/datasets/active")[1] == {"dataset_id": None, "files_base": None, "job": None}
    status, body, _ = request(server, "/api/copilot/status")
    assert status == 200 and body["ready"] is False and body["dataset_id"] is None
    status, body, _ = answer(server)
    assert status == 503 and body["error"]["code"] == "no_dataset"


def test_two_uploads_identical_to_cli_and_persisted_on_restart(service, tmp_path):
    server, manager = service
    files = parquet_files()
    first = upload(server, files)
    second = upload(server, files)
    assert first != second
    cli_data, cli_out = tmp_path / "cli-data", tmp_path / "cli-out"
    cli_data.mkdir()
    for name, content in files.items():
        (cli_data / f"{name}.parquet").write_bytes(content)
    cli(["--data", str(cli_data), "--out", str(cli_out)])
    for filename in OUTPUT_FILES:
        expected = (cli_out / filename).read_bytes()
        for version in (first, second):
            status, raw, headers = request(server, f"/api/datasets/{version}/files/{filename}")
            assert status == 200 and raw == expected
            assert headers["X-Dataset-Version"] == version
            assert "attachment" in headers["Content-Disposition"]
    restored = DatasetManager(manager.root)
    assert restored.active()["dataset_id"] == second
    assert restored.snapshot().backend.tools.store.has(ISOLATED_GID)
    assert restored.output_file(first, "nodes_roles.csv").read_bytes() == (cli_out / "nodes_roles.csv").read_bytes()


@pytest.mark.parametrize("variant", ["missing", "duplicate", "filename", "path", "unknown"])
def test_bad_file_selection_releases_slot_and_keeps_active(service, variant):
    server, manager = service
    prior = upload(server)
    files = parquet_files()
    options = {}
    if variant == "missing":
        files.pop("nodes")
    elif variant == "duplicate":
        options["names"] = ["nodes", "nodes", "edges", "transactions"]
    elif variant == "filename":
        options["filename"] = "nodes.csv"
    elif variant == "path":
        options["filename"] = "../../nodes.parquet"
    else:
        files["unexpected"] = b"unwanted"
    status, body, _ = submit(server, files, **options)
    assert status == 400 and body["error"]
    assert manager.active()["dataset_id"] == prior
    assert upload(server) != prior  # rejection did not leak the busy slot


@pytest.mark.parametrize("variant, expected", [
    ("corrupt", "Parquet"), ("schema", "колонок"), ("gid", "целые"),
    ("unknown_gid", "отсутствуют"), ("sum", "неположительных"),
    ("infinite", "конечные"),
])
def test_invalid_parquet_and_contract_preserve_previous(service, variant, expected):
    server, manager = service
    prior = upload(server)

    def change(frames):
        if variant == "schema":
            frames["nodes"].drop(columns="gid", inplace=True)
        elif variant == "gid":
            frames["nodes"]["gid"] = frames["nodes"].gid.astype(float)
            frames["nodes"].loc[0, "gid"] = 1.5
        elif variant == "unknown_gid":
            frames["nodes"].loc[0, "gid"] = 999
        elif variant == "sum":
            frames["transactions"].loc[0, "sum_kzt"] = 0
        elif variant == "infinite":
            frames["transactions"].loc[0, "sum_kzt"] = float("inf")
            frames["edges"].loc[0, "sum_kzt"] = float("inf")

    files = parquet_files(change)
    if variant == "corrupt":
        files["nodes"] = b"not a parquet"
    status, job, _ = submit(server, files)
    assert status == 202
    result = wait_job(server, job)
    assert result["state"] == "error" and expected in result["error"]
    assert manager.active()["dataset_id"] == prior
    assert manager.snapshot().backend.tools.store.has(ISOLATED_GID)
    assert DatasetManager(manager.root).active()["dataset_id"] == prior


@pytest.mark.parametrize("table, column, value, dtype", [
    ("nodes", "gid", 2**63, "uint64"),
    ("edges", "src", 2**63, "uint64"),
    ("edges", "dst", 2**63, "uint64"),
    ("transactions", "src", 2**63, "uint64"),
    ("transactions", "dst", 2**63, "uint64"),
    ("nodes", "gid", 2**64 - 1, "uint64"),
    ("nodes", "gid", str(-(2**63) - 1), "str"),
    ("nodes", "gid", str(2**63) + ".0", "str"),
    ("nodes", "gid", float(2**63), "float64"),
])
def test_id_overflow_rejected_before_coercion(service, table, column, value, dtype):
    server, manager = service
    prior = upload(server)

    def change(frames):
        frames[table][column] = frames[table][column].astype(dtype)
        frames[table].loc[0, column] = value

    status, job, _ = submit(server, parquet_files(change))
    assert status == 202
    result = wait_job(server, job)
    assert result["state"] == "error"
    assert f"{table}.parquet" in result["error"] and column in result["error"] and "int64" in result["error"]
    assert manager.active()["dataset_id"] == prior
    assert manager.output_file(job["job_id"], "nodes_roles.csv") is None


@pytest.mark.parametrize("gid", [-(2**63), 0, 2**63 - 1])
def test_signed_int64_boundary_ids_remain_exact(service, gid):
    server, manager = service

    def change(frames):
        nodes = frames["nodes"]
        nodes.loc[nodes.gid == ISOLATED_GID, "gid"] = gid

    version = upload(server, parquet_files(change))
    assert manager.snapshot().backend.tools.store.has(gid)
    status, csv, _ = request(server, f"/api/datasets/{version}/files/nodes_roles.csv")
    assert status == 200 and f"{gid},".encode() in csv


def test_request_and_file_size_limits(service):
    server, manager = service
    body, headers = multipart(parquet_files())
    headers["Content-Length"] = str(MAX_UPLOAD_BYTES + 1)
    status, error, _ = request(server, "/api/datasets/jobs", method="POST", headers=headers)
    assert status == 413 and "32 МиБ" in error["error"]
    files = parquet_files()
    files["nodes"] = b"x" * (MAX_FILE_BYTES + 1)
    status, error, _ = submit(server, files)
    assert status == 413 and "10 МиБ" in error["error"]
    assert manager.active()["dataset_id"] is None
    assert upload(server)


def test_duplicate_upload_rejected_during_body_or_calculation(service):
    server, manager = service
    reserved = manager.reserve()  # acquired before HTTP reads multipart body
    status, error, _ = submit(server)
    assert status == 409 and "выполняется" in error["error"]
    manager.fail(reserved, "Test cancelled upload")
    assert upload(server)


@pytest.mark.parametrize("stage", ["pipeline", "backend", "publish"])
def test_failure_does_not_publish_partial_files(service, monkeypatch, stage):
    import agent_orchestrator.datasets as datasets

    server, manager = service
    old = upload(server)

    def broken(*_):
        raise RuntimeError("private filesystem path should never appear")

    if stage == "publish":
        monkeypatch.setattr(datasets.os, "replace", broken)
    else:
        monkeypatch.setattr(datasets, "run_pipeline" if stage == "pipeline" else "backend_from_dir", broken)
    status, job, _ = submit(server)
    assert status == 202
    result = wait_job(server, job)
    assert result["state"] == "error" and "private" not in result["error"]
    assert manager.active()["dataset_id"] == old
    assert request(server, f'/api/datasets/{job["job_id"]}/files/nodes_roles.csv')[0] == 404
    if stage != "backend":
        restored = DatasetManager(manager.root)
        assert restored.active()["dataset_id"] == old
        assert restored.output_file(job["job_id"], "nodes_roles.csv") is None


def test_malformed_multipart_and_empty_files_release_slot(service):
    server, manager = service
    body, headers = multipart(parquet_files())
    status, error, _ = request(server, "/api/datasets/jobs", body=body[:-8], headers=headers)
    assert status == 415 and error["error"]
    files = parquet_files()
    files["nodes"] = b""
    status, error, _ = submit(server, files)
    assert status == 400 and "nodes.parquet" in error["error"]
    assert manager.active()["dataset_id"] is None
    assert upload(server)


def test_copilot_automatically_swaps_version_and_rejects_stale_requests(service):
    server, _ = service
    old = upload(server)
    status, result, headers = answer(server, old)
    assert status == 200 and result["status"] == "ok"
    assert headers["X-Dataset-Version"] == old
    assert all(int(gid) < 100 for gid in result["gids"])
    current = upload(server, parquet_files(offset=100))
    status, result, _ = answer(server, old)
    assert status == 409 and result["error"]["code"] == "dataset_changed"
    status, result, headers = answer(server, current)
    assert status == 200 and result["status"] == "ok" and result["verification"] == "passed"
    assert result["gids"] and all(int(gid) > 100 for gid in result["gids"])
    assert headers["X-Dataset-Version"] == current
    assert request(server, "/api/copilot/status")[1]["dataset_id"] == current


def test_late_answer_keeps_original_snapshot_and_version(service, monkeypatch):
    import agent_orchestrator.server as server_module

    server, _ = service
    old = upload(server)
    started, release = threading.Event(), threading.Event()
    actual_run = server_module.run

    def delayed(request_, backend):
        started.set()
        assert release.wait(timeout=10)
        return actual_run(request_, backend)

    monkeypatch.setattr(server_module, "run", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(answer, server, old)
        try:
            assert started.wait(timeout=5)
            current = upload(server, parquet_files(offset=100))
            assert current != old
        finally:
            release.set()
        status, result, headers = future.result(timeout=10)
    assert status == 200 and headers["X-Dataset-Version"] == old
    assert result["gids"] and all(int(gid) < 100 for gid in result["gids"])


def test_upload_origin_and_output_paths_are_restricted(service):
    server, _ = service
    body, headers = multipart(parquet_files())
    headers["Origin"] = "https://attacker.example"
    assert request(server, "/api/datasets/jobs", body=body, headers=headers)[0] == 403
    version = upload(server)
    for path in (f"/api/datasets/{version}/files/../data/nodes.parquet",
                 f"/api/datasets/{version}/files/run_report.json",
                 "/api/datasets/../../active.json/files/nodes_roles.csv",
                 "/api/datasets/jobs/missing"):
        assert request(server, path)[0] == 404


def test_import_legacy_out_as_immutable_snapshot(service, tmp_path):
    server, manager = service
    original = upload(server)
    out_dir = manager.output_file(original, "nodes_roles.csv").parent
    imported = DatasetManager(tmp_path / "fresh-state", legacy_out=out_dir)
    version = imported.active()["dataset_id"]
    assert version and version != original
    assert imported.active()["files_base"] == f"/api/datasets/{version}/files"
    assert imported.snapshot().backend.tools.store.has(ISOLATED_GID)
    assert imported.output_file(version, "nodes_roles.csv").read_bytes() == (out_dir / "nodes_roles.csv").read_bytes()
