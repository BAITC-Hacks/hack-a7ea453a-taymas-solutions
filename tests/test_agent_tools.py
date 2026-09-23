"""PAN-45: read-only инструменты графа для агента.

Синтетический граф с заранее известными ответами; выгрузки строит настоящий
пайплайн (python -m money_graph), инструменты читают их с диска, как в проде.

    S1 ─100к→ C ←50к─ S2 ─10к→ L            C — общий сборщик S1 и S2 напрямую
    S1 ─30к→ M ─30к→ K ←20к─ N ←20к─ S3     K — общий сборщик S1 и S3 через посредников
    C ─140к→ D ─100к→ E ─90к→ F (4-е колено) цепочка вниз до границы обхода
    I — seed без переводов
"""

import hashlib
import json

import pandas as pd
import pytest

from agent_tools import (RESULTS, TOOL_NAMES, GraphStore, GraphTools, InvalidArgumentError,
                         UnknownClusterError, UnknownGidError, call_tool, tool_specs, validate)
from money_graph.cli import main as run_pipeline

from tests._mini import C, D, DATA, E, F, HAS_DATA, I, K, L, M, N, S1, S2, S3, build_outputs


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory):
    return build_outputs(tmp_path_factory.mktemp("agent"))


@pytest.fixture(scope="module")
def tools(out_dir):
    return GraphTools(GraphStore.from_dir(out_dir))


@pytest.fixture(scope="module")
def csv(out_dir):
    return {"nodes": pd.read_csv(out_dir / "nodes_roles.csv").set_index("gid"),
            "edges": pd.read_csv(out_dir / "edge_table.csv").set_index(["src", "dst"])}


def _codes(result):
    return {w["code"] for w in result["warnings"]}


# ---------------------------------------------------------------- узел и прямые связи

def test_get_node_matches_nodes_roles(tools, csv):
    r = tools.get_node(C)
    row = csv["nodes"].loc[C]
    for col in ("role", "role_score", "priority_score", "cluster_id", "in_kzt", "out_kzt", "in_tx", "evidence"):
        assert r["node"][col] == row[col], col
    assert r["sources"][0] == {"file": "nodes_roles.csv", "gid": C, "columns": r["sources"][0]["columns"]}
    assert "evidence" in r["sources"][0]["columns"]


def test_incoming_edges_and_totals_match_csv(tools, csv):
    r = tools.get_incoming(C)
    assert [(e["src"], e["sum_kzt"]) for e in r["edges"]] == [(S1, 100_000.0), (S2, 50_000.0)]   # по сумме
    assert r["totals"] == {"n_edges": 2, "sum_kzt": csv["nodes"].loc[C, "in_kzt"], "n_tx": csv["nodes"].loc[C, "in_tx"]}
    for e in r["edges"]:
        assert e["sum_kzt"] == csv["edges"].loc[(e["src"], C), "sum_kzt"]
    assert {"file": "edge_table.csv", "src": S1, "dst": C, "columns": ["sum_kzt", "n_tx"]} in r["sources"]
    assert "inflow_sample_only" in _codes(r)


def test_outgoing_aggregates_transactions(tools):
    r = tools.get_outgoing(C)
    assert r["totals"] == {"n_edges": 1, "sum_kzt": 140_000.0, "n_tx": 2}
    assert r["edges"][0]["dst"] == D and r["edges"][0]["counterparty"]["gid"] == D


def test_depth4_outgoing_warns_instead_of_terminal(tools):
    r = tools.get_outgoing(F)
    assert r["edges"] == [] and r["totals"]["n_edges"] == 0
    assert "depth4_outflow_unobserved" in _codes(r)
    assert tools.get_node(F)["node"]["role"] != "terminal"


def test_unknown_gid_is_typed_error(tools):
    with pytest.raises(UnknownGidError) as exc:
        tools.get_node(999)
    assert exc.value.code == "unknown_gid" and exc.value.details["gid"] == 999
    resp = call_tool(tools, "get_incoming", {"gid": "999"})
    assert resp["ok"] is False and resp["error"]["code"] == "unknown_gid"
    assert call_tool(tools, "compare_nodes", {"gids": [C, 999]})["error"]["type"] == "UnknownGidError"


# ---------------------------------------------------------------- общий сборщик

def test_common_collector_direct_and_via_intermediaries(tools):
    r = tools.find_common_collectors([S1, S2, S3])
    by_gid = {c["gid"]: c for c in r["collectors"]}
    assert r["collectors"][0]["gid"] == C                         # прямые 150 тыс. — первым
    assert by_gid[C]["n_sources"] == 2 and by_gid[C]["direct_sum_kzt"] == 150_000.0
    assert [(p["gid"], p["hops"], p["direct_sum_kzt"]) for p in by_gid[C]["sources_reached"]] == \
        [(S1, 1, 100_000.0), (S2, 1, 50_000.0)]
    assert [(p["gid"], p["hops"]) for p in by_gid[K]["sources_reached"]] == [(S1, 2), (S3, 2)]
    assert by_gid[K]["direct_sum_kzt"] == 0.0                     # через посредников суммы не складываются
    assert L not in by_gid                                         # L получает только от S2


def test_common_collector_empty_result(tools):
    r = tools.find_common_collectors([L, F])
    assert r["collectors"] == [] and r["total_count"] == 0 and "ни один узел" in r["message"]


def test_common_collector_needs_two_gids(tools):
    with pytest.raises(InvalidArgumentError):
        tools.find_common_collectors([S1])


# ---------------------------------------------------------------- обходы

def test_trace_downstream_reaches_boundary(tools, csv):
    r = tools.trace_downstream(C)
    assert [(n["gid"], n["distance"]) for n in r["nodes"]] == [(D, 1), (E, 2), (F, 3)]
    assert [(e["src"], e["dst"]) for e in r["edges"]] == [(C, D), (D, E), (E, F)]
    assert r["edges"][0]["sum_kzt"] == csv["edges"].loc[(C, D), "sum_kzt"]
    frontier = [w for w in r["warnings"] if w["code"] == "depth4_frontier"]
    assert frontier and frontier[0]["gids"] == [F]


def test_trace_depth_limits_hops(tools):
    assert [n["gid"] for n in tools.trace_downstream(S1, depth=1)["nodes"]] == sorted([C, M], key=lambda g: (
        -tools.store.nodes.priority_score[g], g))
    with pytest.raises(InvalidArgumentError):
        tools.trace_downstream(S1, depth=5)


def test_trace_upstream_finds_seeds(tools):
    r = tools.trace_upstream([K])
    assert {(n["gid"], n["distance"]) for n in r["nodes"]} == {(M, 1), (N, 1), (S1, 2), (S3, 2)}
    assert r["seeds_reached"] == [S1, S3]
    assert "upstream_partial" in _codes(r)


def test_trace_from_isolated_seed_is_empty(tools):
    r = tools.trace_downstream(I)
    assert r["nodes"] == [] and r["edges"] == [] and "no_transfers" in _codes(r)


# ---------------------------------------------------------------- рейтинг, кластер, сравнение

def test_rank_candidates_sorted_and_filtered(tools, csv):
    r = tools.rank_candidates(limit=5)
    scores = [(c["priority_score"], c["gid"]) for c in r["candidates"]]
    assert scores == sorted(scores, key=lambda t: (-t[0], t[1])) and r["candidates"][0]["rank"] == 1
    seeds = tools.rank_candidates({"is_seed": True}, limit=50)
    assert {c["gid"] for c in seeds["candidates"]} == {S1, S2, S3, I}
    assert tools.rank_candidates({"role": "no_such_role"})["candidates"] == []
    with pytest.raises(InvalidArgumentError, match="неизвестные фильтры"):
        tools.rank_candidates({"delete": True})


def test_get_cluster_matches_clusters_csv(tools, out_dir):
    clusters = pd.read_csv(out_dir / "clusters.csv").set_index("cluster_id")
    cid = int(tools.get_node(C)["node"]["cluster_id"])
    r = tools.get_cluster(cid)
    assert r["cluster"]["n_nodes"] == clusters.loc[cid, "n_nodes"] == r["members_count"]
    assert r["cluster"]["sum_kzt_internal"] == clusters.loc[cid, "sum_kzt_internal"]
    assert sum(r["roles"].values()) == r["members_count"]
    with pytest.raises(UnknownClusterError):
        tools.get_cluster(10_000)


def test_compare_nodes_links_and_shared_payers(tools):
    r = tools.compare_nodes([C, L, D])
    assert {(x["src"], x["dst"]) for x in r["direct_links"]} == {(C, D)}
    assert {"gid": S2, "with": [C, L]} in r["shared_payers"]
    order = [n["gid"] for n in r["nodes"]]
    assert order == sorted(order, key=lambda g: (-tools.store.nodes.priority_score[g], g))


# ---------------------------------------------------------------- контракт

def test_every_tool_output_matches_schema_and_is_json(tools):
    calls = {"get_node": {"gid": C}, "get_incoming": {"gid": K}, "get_outgoing": {"gid": S1},
             "trace_upstream": {"gids": [K, D]}, "trace_downstream": {"gid": S1},
             "find_common_collectors": {"gids": [S1, S2, S3]}, "rank_candidates": {"filters": {"depth": [1, 2]}},
             "get_cluster": {"cluster_id": int(tools.get_node(C)["node"]["cluster_id"])},
             "compare_nodes": {"gids": [C, K]}}
    assert set(calls) == set(TOOL_NAMES) == {s["function"]["name"] for s in tool_specs()}
    for name, args in calls.items():
        resp = call_tool(tools, name, args)
        assert resp["ok"], resp
        assert validate(resp["result"], RESULTS[name]) == [], name
        assert json.loads(json.dumps(resp)) == resp                   # чистый JSON


def test_allow_list_and_argument_checks(tools):
    assert call_tool(tools, "write_csv", {"path": "out"})["error"]["code"] == "tool_not_allowed"
    assert call_tool(tools, "__init__", {})["error"]["code"] == "tool_not_allowed"
    assert call_tool(tools, "get_node", {"gid": C, "extra": 1})["error"]["code"] == "invalid_argument"
    assert call_tool(tools, "get_node", {"gid": 1.0e17})["error"]["code"] == "invalid_argument"
    assert call_tool(tools, "get_node", {"gid": str(C)})["ok"]


def test_tools_are_deterministic_and_read_only(tools, out_dir):
    digest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out_dir.glob("*.csv")}
    first = call_tool(tools, "find_common_collectors", {"gids": [S3, S1, S2]})
    again = GraphTools(GraphStore.from_dir(out_dir))
    assert call_tool(again, "find_common_collectors", {"gids": [S3, S1, S2]}) == first
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out_dir.glob("*.csv")} == digest


# ---------------------------------------------------------------- реальный датасет

@pytest.mark.skipif(not HAS_DATA, reason="нет data/edges.parquet — распакуйте архив данных в ./data")
def test_real_top5_match_csv(tmp_path):
    run_pipeline(["--data", str(DATA), "--out", str(tmp_path)])
    tools = GraphTools(GraphStore.from_dir(tmp_path))
    nodes = pd.read_csv(tmp_path / "nodes_roles.csv").set_index("gid")
    top5 = pd.read_csv(tmp_path / "top_nodes.csv").gid.head(5).tolist()
    for g in top5:
        node = tools.get_node(g)["node"]
        assert node["role"] == nodes.loc[g, "role"] and node["priority_score"] == nodes.loc[g, "priority_score"]
        inc, out = tools.get_incoming(g, limit=200), tools.get_outgoing(g, limit=200)
        assert inc["totals"]["sum_kzt"] == pytest.approx(nodes.loc[g, "in_kzt"], abs=0.01)
        assert out["totals"]["sum_kzt"] == pytest.approx(nodes.loc[g, "out_kzt"], abs=0.01)
        assert inc["totals"]["n_tx"] == nodes.loc[g, "in_tx"] and out["totals"]["n_edges"] == nodes.loc[g, "out_deg"]
    r = tools.find_common_collectors(top5)
    assert validate(r, RESULTS["find_common_collectors"]) == []
