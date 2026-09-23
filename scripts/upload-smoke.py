#!/usr/bin/env python3
"""Exercise the browser upload API using stdlib only; replaces the active dataset."""

import argparse
import csv
import hashlib
import io
import json
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8501")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--compare-out", type=Path, help="Compare CSV bytes with CLI output from the same environment")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    def request(path, body=None, headers=None):
        with urlopen(Request(base + path, data=body, headers=headers or {}), timeout=60) as response:
            return response.read(), response.headers

    def get_json(path):
        return json.loads(request(path)[0])

    deadline = time.monotonic() + 30
    while True:
        try:
            get_json("/api/datasets/active")
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Local upload API did not become ready") from None
            time.sleep(1)

    boundary = "smoke-" + uuid.uuid4().hex
    parts = []
    for name in ("nodes", "edges", "transactions"):
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                      f'filename="{name}.parquet"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
                     + (args.data / f"{name}.parquet").read_bytes() + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    started = time.monotonic()
    job = json.loads(request("/api/datasets/jobs", body,
                             {"Content-Type": f"multipart/form-data; boundary={boundary}"})[0])
    deadline = time.monotonic() + 300
    while job["state"] not in {"ready", "error"}:
        if time.monotonic() >= deadline:
            raise RuntimeError("Pipeline exceeded 300 seconds")
        time.sleep(0.2)
        job = get_json("/api/datasets/jobs/" + job["job_id"])
    assert job["state"] == "ready", job
    assert job["elapsed_seconds"] <= 300, job
    active = get_json("/api/datasets/active")
    assert active["dataset_id"] == job["dataset_id"]
    tables = {}
    for name in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv", "edge_table.csv"):
        raw, _ = request(active["files_base"] + "/" + name)
        tables[name] = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        if args.compare_out:
            assert raw == (args.compare_out / name).read_bytes(), f"CLI/upload mismatch: {name}"
        print(f"{name}: {len(tables[name])} rows, sha256={hashlib.sha256(raw).hexdigest()}")
    nodes = tables["nodes_roles.csv"]
    assert nodes and len(tables["top_nodes.csv"]) >= min(20, len(nodes))
    assert all(n["role"] and n["evidence"] and n["cluster_id"] for n in nodes)
    status = get_json("/api/copilot/status")
    assert status["ready"] and status["dataset_id"] == active["dataset_id"], status
    answer, headers = request("/api/copilot/answer", json.dumps({
        "question": "Кого проверить первым и почему?", "selected_gids": [], "use_nvidia": False,
    }).encode(), {"Content-Type": "application/json", "X-Dataset-Version": active["dataset_id"]})
    answer = json.loads(answer)
    assert headers.get("X-Dataset-Version") == active["dataset_id"]
    assert answer["verification"] == "passed" and answer["claims"], answer
    print(f"Upload + CSV + Copilot passed in {time.monotonic() - started:.2f}s; "
          f"pipeline {job['elapsed_seconds']:.3f}s; dataset {active['dataset_id']}")


if __name__ == "__main__":
    main()
