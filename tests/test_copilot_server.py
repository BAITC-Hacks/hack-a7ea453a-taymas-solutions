"""Local HTTP integration: real tools/verifier, never NVIDIA or external network."""

import hashlib
import http.client
import json
import threading

import pytest

from agent_orchestrator import GraphBackend
from agent_orchestrator.server import MAX_REQUEST_BYTES, make_server
from agent_tools import GraphStore, GraphTools
from tests._mini import build_outputs


@pytest.fixture(scope="module")
def outputs(tmp_path_factory):
    return build_outputs(tmp_path_factory.mktemp("copilot-server"))


@pytest.fixture
def server(outputs, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    service = make_server(GraphBackend(GraphTools(GraphStore.from_dir(outputs))), port=0)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    yield service
    service.shutdown()
    service.server_close()
    thread.join(timeout=2)


def request(server, method="GET", path="/api/copilot/status", body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(method, path, body=body.encode("utf-8") if isinstance(body, str) else body, headers=headers or {})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_status_does_not_call_nvidia_or_expose_configuration(server, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-only-secret")
    monkeypatch.setenv("NVIDIA_MODEL", "test-model")
    status, body = request(server)
    assert status == 200 and body == {"ready": True, "nvidia_available": True, "dataset_id": None}
    assert "test-only-secret" not in json.dumps(body)


def test_actual_answer_has_browser_gids_and_verified_facts(server, outputs):
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs.glob("*.csv")}
    status, result = request(server, "POST", "/api/copilot/answer", json.dumps({
        "question": "Кого проверить первым и почему?", "selected_gids": [], "use_nvidia": True,
    }), {"Content-Type": "application/json", "Origin": "http://localhost:5173"})
    assert status == 200 and result["status"] == "ok"
    assert result["provider"] == "fallback" and result["fallback_reason"] == "no_api_key"
    assert result["verification"] == "passed"
    assert result["claims"] and all(type(g) is str for g in result["gids"])
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs.glob("*.csv")}


@pytest.mark.parametrize("body", ["not json", "null", "[]", '{"question":"Почему?","file":"/tmp/data"}',
                                  '{"question":"Почему?","selected_gids":[1.1]}'])
def test_invalid_requests_are_typed(server, body):
    status, result = request(server, "POST", "/api/copilot/answer", body, {"Content-Type": "application/json"})
    assert status == 400 and result["error"]["code"] == "invalid_request"


def test_limits_content_type_and_origin(server):
    status, _ = request(server, "POST", "/api/copilot/answer", "x" * (MAX_REQUEST_BYTES + 1), {"Content-Type": "application/json"})
    assert status == 413
    status, _ = request(server, "POST", "/api/copilot/answer", "{}", {"Content-Type": "text/plain"})
    assert status == 415
    status, _ = request(server, headers={"Origin": "https://example.com"})
    assert status == 403
    status, _ = request(server, headers={"Host": "example.com"})
    assert status == 403
    status, _ = request(server, path="/data/nodes.parquet")
    assert status == 404
