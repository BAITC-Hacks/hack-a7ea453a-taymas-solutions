"""PAN-47: evaluation-набор из пяти AML-вопросов и отчёт pass/fail."""

import copy
import json
import socket

import pytest

from agent_tools import GraphStore, GraphTools
from agent_tools.evaluation import build_cases, check_expectations, main, render_markdown, run_eval, template_answer

from tests._mini import C, DATA, F, HAS_DATA, I, L, build_outputs


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory):
    return build_outputs(tmp_path_factory.mktemp("eval"))


@pytest.fixture(scope="module")
def tools(out_dir):
    return GraphTools(GraphStore.from_dir(out_dir))


def test_cases_cover_required_topics(tools):
    cases = {c.id: c for c in build_cases(tools)}
    assert set(cases) == {"priority", "common_collector", "trace", "depth4", "empty"}
    assert cases["priority"].expect["include_gids"] == [int(tools.store.top.gid.iat[0])]
    assert cases["common_collector"].expect["include_gids"] == [C]          # собирает у двух seed
    assert cases["depth4"].expect["include_gids"] == [F]
    assert cases["depth4"].expect["not_role"] == {F: "terminal"}
    assert cases["empty"].expect == {"include_gids": [I, L], "empty": True}
    assert {c["tool"] for c in cases["trace"].tool_calls} == {"trace_upstream", "trace_downstream"}


def test_template_answerer_passes_all(tools):
    report = run_eval(template_answer, tools, provider="template")
    assert report["passed"] == report["total"] == 5, [
        (r["id"], r["verifier_errors"], r["expectation_failures"]) for r in report["cases"]]
    assert "5/5" in render_markdown(report)


def test_hallucinating_answerer_fails(tools):
    def liar(case, tools):
        ans = template_answer(case, tools)
        for c in ans["claims"]:
            if c.get("field") in ("sum_kzt", "in_kzt"):
                c["value"] = c["value"] * 2
        ans["gids"] = [g for g in ans["gids"] if g not in case.expect.get("include_gids", [])[:1]]
        return ans

    report = run_eval(liar, tools, provider="liar")
    assert report["passed"] < report["total"]
    failed = {r["id"]: r for r in report["cases"] if not r["passed"]}
    assert any("value_mismatch" in e for e in failed["depth4"]["verifier_errors"])
    assert any("нет ожидаемых gid" in f for f in failed["priority"]["expectation_failures"])


def test_expectations_catch_missing_warning_and_non_empty(tools):
    cases = {c.id: c for c in build_cases(tools)}
    ans = copy.deepcopy(template_answer(cases["depth4"], tools))
    ans["warnings"] = []
    assert any("depth4_outflow_unobserved" in f for f in check_expectations(ans, cases["depth4"]))
    ans = copy.deepcopy(template_answer(cases["empty"], tools))
    ans["status"] = "ok"
    assert check_expectations(ans, cases["empty"]) == ["ожидался пустой результат (status=empty)"]


def test_crashing_answerer_is_a_failed_case_not_a_crash(tools):
    def broken(case, tools):
        raise TimeoutError("NVIDIA API timeout")

    report = run_eval(broken, tools, provider="nvidia")
    assert report["passed"] == 0 and report["total"] == 5
    assert "TimeoutError" in report["cases"][0]["expectation_failures"][0]


def test_runs_without_network_and_api_key(tools, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    def no_network(*args, **kwargs):
        raise AssertionError("сетевой вызов запрещён в evaluation")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    assert run_eval(template_answer, tools)["passed"] == 5


def test_cli_writes_report(out_dir, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--out", str(out_dir), "--report", str(tmp_path / "eval.md")])
    assert exc.value.code == 0
    assert "5/5" in (tmp_path / "eval.md").read_text(encoding="utf-8")
    data = json.loads((tmp_path / "eval.json").read_text(encoding="utf-8"))
    assert data["passed"] == 5 and len(data["cases"]) == 5
    assert "PASS" in capsys.readouterr().out


@pytest.mark.skipif(not HAS_DATA, reason="нет data/edges.parquet — распакуйте архив данных в ./data")
def test_real_dataset_eval(tmp_path):
    out = tmp_path / "out"
    from money_graph.cli import main as run_pipeline
    run_pipeline(["--data", str(DATA), "--out", str(out)])
    tools = GraphTools(GraphStore.from_dir(out))
    report = run_eval(template_answer, tools, provider="template")
    assert report["passed"] == 5, [(r["id"], r["verifier_errors"], r["expectation_failures"]) for r in report["cases"]]
    d4 = next(r for r in report["cases"] if r["id"] == "depth4")
    assert tools.store.nodes.at[d4["gids"][0], "depth"] == 4
