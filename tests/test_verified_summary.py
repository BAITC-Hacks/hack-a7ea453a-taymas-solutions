"""PAN-62: known numbers do not validate their meaning in arbitrary prose."""

import copy

import pytest

from agent_orchestrator import answer
from agent_tools import GraphStore, GraphTools
from agent_tools.answer import render_summary
from agent_tools.verifier import mark_unverified, verify
from tests._mini import C, M, S1, S2, build_outputs


@pytest.fixture(scope="module")
def tools(tmp_path_factory):
    return GraphTools(GraphStore.from_dir(build_outputs(tmp_path_factory.mktemp("summary"))))


@pytest.fixture
def brief(tools):
    result = answer("Кто общий сборщик?", tools, selected_gids=[S1, S2])
    assert result["verification"] == "passed"
    return result


@pytest.mark.parametrize("mutation", ["flows", "gid", "role", "edge", "unrelated_amount", "next_step"])
def test_known_values_do_not_validate_false_prose(tools, brief, mutation):
    node = tools.store.node(C)
    edge = tools.store.edges.query("src == @S1 and dst == @C").iloc[0]
    texts = {
        "flows": f"gid {C} получил {node.out_kzt} KZT и отправил {node.in_kzt} KZT.",
        "gid": f"gid {S1} получил {node.in_kzt} KZT.",
        "role": f"gid {C}: роль terminal.",
        "edge": f"Перевод {C} → {S1}: {edge.sum_kzt} KZT.",
        "unrelated_amount": f"gid {C} получил {tools.store.node(M).in_kzt} KZT.",
        "next_step": f"gid {C} получил {node.out_kzt} KZT.",
    }
    field = "next_steps" if mutation == "next_step" else "summary"
    text = texts[mutation] + " Это гипотеза для проверки."
    brief[field] = [text] if field == "next_steps" else text
    report = verify(brief, tools)
    assert not report["ok"], mutation
    assert "untraced_number" not in {e["code"] for e in report["errors"]}


def test_backend_does_not_publish_tampered_summary(tools, monkeypatch):
    from agent_orchestrator import orchestrator

    original = orchestrator.compose

    def tampered(*args, **kwargs):
        result = original(*args, **kwargs)
        result["summary"] = f"gid {C}: роль terminal. Это гипотеза для проверки."
        return result

    monkeypatch.setattr(orchestrator, "compose", tampered)
    result = answer("Почему этот узел в топе?", tools, selected_gids=[C])
    assert result["status"] == "error"
    assert result["error"]["code"] == "invalid_evidence"
    assert result["verification"] != "passed"
    assert not result["claims"]


def test_marking_invalid_text_clears_verification(tools, brief):
    brief["summary"] = f"gid {C}: роль terminal. Это гипотеза для проверки."
    before = copy.deepcopy(brief)
    marked = mark_unverified(brief, verify(brief, tools))
    assert marked["verification"] != "passed"
    assert marked["summary"] != brief["summary"]
    assert brief == before


@pytest.mark.parametrize("mutation", ["flows", "gid", "role", "edge"])
def test_rendering_changed_claims_does_not_verify_them(tools, brief, mutation):
    if mutation == "edge":
        claim = next(c for c in brief["claims"] if c["kind"] == "edge" and c["src"] == S1)
        claim["src"], claim["dst"] = claim["dst"], claim["src"]
        claim["source"].update(src=claim["src"], dst=claim["dst"])
    else:
        field = "role" if mutation == "role" else "in_kzt"
        claim = next(c for c in brief["claims"] if c["kind"] == "node" and c["gid"] == C and c["field"] == field)
        if mutation == "gid":
            # Remove the original S1 fact so a conflicting duplicate is not the reason for rejection.
            brief["claims"] = [c for c in brief["claims"]
                               if not (c["kind"] == "node" and c["gid"] == S1 and c["field"] == field)]
            claim["gid"] = claim["source"]["gid"] = S1
        else:
            claim["value"] = "terminal" if mutation == "role" else float(tools.store.node(C).out_kzt)
    brief["summary"] = render_summary(brief)
    report = verify(brief, tools)
    assert not report["ok"]
    assert {e["code"] for e in report["errors"]} & {"value_mismatch", "unknown_edge"}


def test_summary_has_exact_direction_and_claim_values(tools, brief):
    before = copy.deepcopy(brief)
    text = render_summary(brief)
    assert f"gid {C}: роль consolidator" in text
    assert "вход 150 000 KZT" in text and "выход 140 000 KZT" in text
    assert f"Перевод {S1} → {C}: сумма 100 000 KZT" in text
    assert brief == before
    assert verify(brief, tools)["ok"]
