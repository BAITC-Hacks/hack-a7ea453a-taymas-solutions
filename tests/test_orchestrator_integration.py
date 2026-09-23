"""Runs with PAN-45/PAN-47 installed; no duplicate implementation in this PR."""

import json

import pytest

agent_tools = pytest.importorskip("agent_tools", reason="Integration requires PAN-45")
verifier = pytest.importorskip("agent_tools.verifier", reason="Integration requires PAN-47")

from agent_tools.evaluation import build_cases, run_eval
from agent_orchestrator import GraphBackend, Request, answer, for_browser, run
from agent_orchestrator.orchestrator import answer_case
from money_graph.pipeline import run as pipeline
from tests._mini import DATA, HAS_DATA, write_mini_parquet
from tests.test_orchestrator import Client


def graph(result):
    t = result.tables
    return agent_tools.GraphTools(agent_tools.GraphStore(
        t["nodes_roles.csv"], t["edge_table.csv"], t["clusters.csv"], t["top_nodes.csv"]))


@pytest.fixture(scope="module")
def mini(tmp_path_factory):
    folder = write_mini_parquet(tmp_path_factory.mktemp("pan46") / "data")
    return graph(pipeline(folder))


def test_actual_tools_and_verifier_without_network(mini, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    gid = int(mini.store.top.gid.iat[0])
    before = mini.store.nodes.copy(deep=True)
    result = answer("Почему этот узел в топе?", mini, selected_gids=[gid], use_nvidia=True)
    assert result["status"] == "ok"
    assert result["fallback_reason"] == "no_api_key"
    assert result["verification"] == "passed"
    assert verifier.verify(result, mini)["ok"]
    assert mini.store.nodes.equals(before)
    assert for_browser(result)["gids"] == [str(gid)]


def test_depth4_and_empty_with_actual_tools(mini):
    gid = int(mini.store.nodes[mini.store.nodes.depth == 4].gid.iat[0])
    result = answer("Является ли узел конечным получателем?", mini, selected_gids=[gid])
    assert result["verification"] == "passed"
    assert any(w["code"] == "depth4_outflow_unobserved" for w in result["warnings"])
    no_out = [int(g) for g in mini.store.nodes[mini.store.nodes.out_deg == 0].gid.iloc[:2]]
    result = answer("Кто общий сборщик?", mini, selected_gids=no_out)
    assert result["status"] == "empty"
    assert verifier.verify(result, mini)["ok"]


@pytest.mark.skipif(not HAS_DATA, reason="Organizer dataset not installed")
def test_five_real_evaluation_questions_local_and_simulated_nvidia(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    tools = graph(pipeline(DATA))
    before = {name: getattr(tools.store, name).copy(deep=True) for name in ("nodes", "edges", "clusters", "top")}
    report = run_eval(answer_case, tools)
    assert report["passed"] == report["total"] == 5, [
        (r["id"], r["verifier_errors"], r["expectation_failures"]) for r in report["cases"]]
    for case in build_cases(tools):
        request = Request(case.question, use_nvidia=True)
        result = run(request, GraphBackend(tools), client=Client(request))
        assert result["provider"] == "nvidia"
        assert verifier.verify(result, tools)["ok"]
        assert len(json.dumps(for_browser(result), ensure_ascii=False).encode()) < 100_000
    for name, frame in before.items():
        assert getattr(tools.store, name).equals(frame)
