"""C2 depends on existence of a long directed cycle, not the shortest cycle."""

from io import BytesIO

import networkx as nx
import pandas as pd
import pytest

from money_graph import config as C
from money_graph.features import build_features, cycle_features
from money_graph.graph import build_graph, edge_table
from money_graph.io import validate_inputs
from money_graph.pipeline import run
from money_graph.roles import assign_roles
from tests._mini import frames_from_tx


def case_inputs(cycle_lengths=(), payers=3, receivers=10, seed=False, reverse=False):
    """Keep target degrees fixed while adding independent return paths."""
    target = 1
    incoming = list(range(10, 10 + payers))
    outgoing = list(range(20, 20 + receivers))
    paths = []
    for i, length in enumerate(cycle_lengths):
        payer = incoming[i]
        if length == 2:
            outgoing[i] = payer
        else:
            middle = [100 + i * 10 + j for j in range(length - 3)]
            path = [outgoing[i], *middle, payer]
            paths.extend(zip(path, path[1:]))
    pairs = [(p, target) for p in incoming] + [(target, r) for r in outgoing] + paths
    # Include a real boundary leaf and an isolated seed without changing target degrees.
    pairs.append((outgoing[-1], 900))
    depth = {g: 2 for pair in pairs for g in pair}
    depth.update({target: 0 if seed else 1, 900: 4, 999: 0})
    rows = [(src, dst, "2026-07-01", 10000) for src, dst in pairs]
    if reverse:
        rows.reverse()
        depth = dict(reversed(list(depth.items())))
    edges, nodes, tx, _ = validate_inputs(*frames_from_tx(rows, depth))
    return edges, nodes, tx


def case(*args, **kwargs):
    edges, nodes, tx = case_inputs(*args, **kwargs)
    graph = build_graph(edge_table(edges, nodes, tx), nodes)
    return build_features(graph, nodes, tx)


@pytest.mark.parametrize("lengths, expected_rule", [
    ((2, 3), "C2"),  # regression: the short cycle used to mask the long one
    ((2,), "D1"),
    ((3,), "C2"),
    ((5,), "C2"),   # inclusive upper bound
    ((6,), "D1"),   # outside the documented search, not proof of no cycles
    ((), "D1"),
])
def test_c2_classifies_cycles_with_fixed_degrees(lengths, expected_rule):
    roles = assign_roles(case(lengths)).set_index("gid")
    target = roles.loc[1]
    assert target.n_payers == 3 and target.n_receivers == 10
    assert target.role_rule == expected_rule
    assert target.role == ("coordinator" if expected_rule == "C2" else "distributor")
    assert target.has_long_cycle == (expected_rule == "C2")
    if expected_rule == "C2":
        assert "есть направл. цикл длиной 3–5" in target.evidence
        assert "гипотеза координации" in target.evidence
    assert roles.evidence.str.len().max() <= C.EVIDENCE_MAX_LEN
    assert roles.loc[999, "role_rule"] == "isolated"
    assert roles.loc[900, "role_rule"] == "P-trunc"
    assert not ((roles.depth == 4) & (roles.role == "terminal")).any()


def test_c1_keeps_precedence_over_c2():
    assert assign_roles(case((2, 3), payers=5)).set_index("gid").loc[1, "role_rule"] == "C1"


@pytest.mark.parametrize("payers, receivers", [(2, 10), (3, 9)])
def test_long_cycle_does_not_bypass_degree_requirements(payers, receivers):
    assert assign_roles(case((3,), payers=payers, receivers=receivers)).set_index("gid").loc[1, "role_rule"] != "C2"


def test_seed_can_be_coordinator_by_structure():
    target = assign_roles(case((2, 3), seed=True)).set_index("gid").loc[1]
    assert target.role_rule == "C2"
    assert "seed" in target.evidence and "вход извне не виден" in target.evidence


def test_cycle_results_are_independent_of_input_order_and_gid_values():
    first = assign_roles(case((2, 3)))
    second = assign_roles(case((2, 3), reverse=True))
    pd.testing.assert_frame_equal(first, second)
    graph = nx.DiGraph([(1, 2), (2, 1), (1, 3), (3, 4), (4, 1)])
    renamed = {g: 1000 - g * 7 for g in graph}
    a = cycle_features(graph, pd.DataFrame({"gid": list(graph)}))
    b = cycle_features(nx.relabel_nodes(graph, renamed), pd.DataFrame({"gid": [renamed[g] for g in graph]}))
    pd.testing.assert_frame_equal(a.drop(columns="gid"), b.drop(columns="gid"))


@pytest.mark.parametrize("pairs, expected_min, expected_flag", [
    ([(1, 1)], 1, False),
    ([(1, 2), (2, 1)], 2, False),
    ([(1, 2), (2, 3), (1, 3)], 0, False),  # undirected triangle, no directed cycle
    ([(1, 1), (1, 2), (2, 3), (3, 1)], 1, True),
    ([(1, 2), (2, 1), (1, 3), (3, 4), (4, 1)], 2, True),
])
def test_direction_self_loops_and_shortest_cycle_are_preserved(pairs, expected_min, expected_flag):
    graph = nx.DiGraph(pairs)
    df = cycle_features(graph, pd.DataFrame({"gid": [*graph, 999]})).set_index("gid")
    assert df.loc[1, "min_cycle_len"] == expected_min
    assert df.loc[1, "has_long_cycle"] == expected_flag
    assert not df.loc[999, "has_long_cycle"]
    assert df.loc[999, "n_cycles"] == 0 and df.loc[999, "min_cycle_len"] == 0
    assert df.has_long_cycle.dtype == bool


def test_long_cycle_obeys_configured_bounds(monkeypatch):
    monkeypatch.setattr(C, "COORD_CYCLE_MIN_LEN", 4)
    assert not case((3,)).set_index("gid").loc[1, "has_long_cycle"]
    assert case((4,)).set_index("gid").loc[1, "has_long_cycle"]
    monkeypatch.setattr(C, "CYCLE_MAX_LEN", 3)
    assert not case((4,)).has_long_cycle.any()


def test_mixed_cycle_role_and_flag_survive_the_complete_csv_pipeline(tmp_path):
    edges, nodes, tx = case_inputs((2, 3))
    for name, frame in [("edges", edges), ("nodes", nodes), ("transactions", tx)]:
        frame.to_parquet(tmp_path / f"{name}.parquet", index=False)
    result = run(tmp_path)
    nr = pd.read_csv(BytesIO(result.files["nodes_roles.csv"])).set_index("gid")
    top = pd.read_csv(BytesIO(result.files["top_nodes.csv"])).set_index("gid")
    assert nr.has_long_cycle.dtype == bool and not nr.has_long_cycle.isna().any()
    assert nr.loc[1, "has_long_cycle"] and nr.loc[1, "min_cycle_len"] == 2
    assert nr.loc[1, "role_rule"] == "C2"
    assert top.loc[1, "role"] == nr.loc[1, "role"] == "coordinator"
    assert top.loc[1, "priority_score"] == nr.loc[1, "priority_score"]
