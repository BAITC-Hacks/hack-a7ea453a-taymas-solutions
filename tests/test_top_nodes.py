"""Контракт PAN-37 без зависимости от незавершённых PAN-36 и ролей."""

from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analytics.top_nodes import TOP_COLUMNS, audit, run


@pytest.fixture
def tables():
    # Синтетические идентификаторы, заведомо не из банковского датасета.
    gids = np.arange(100, 125, dtype=np.int64)
    nodes = pd.DataFrame({"gid": gids, "is_seed": gids % 2 == 0})
    nc = pd.DataFrame({"gid": gids, "cluster_id": gids % 3})
    edges = pd.DataFrame({"src": gids[:-1], "dst": gids[1:], "sum_kzt": 100.0})
    priority = pd.DataFrame({"gid": gids, "priority_score": 0.5,
                             "why": "Синтетический сигнал: 100 KZT"})
    roles = nc.assign(role="peripheral", priority_score=0.5)
    roles["in_kzt"] = roles.gid.map(edges.groupby("dst").sum_kzt.sum()).fillna(0)
    roles["out_kzt"] = roles.gid.map(edges.groupby("src").sum_kzt.sum()).fillna(0)
    clusters = nc.groupby("cluster_id").size().rename("n_nodes").to_frame()
    clusters["n_seed"] = nodes.assign(cluster_id=nc.cluster_id).groupby("cluster_id").is_seed.sum()
    clusters["sum_kzt_internal"] = 0.0
    return priority, roles, edges, nodes, nc, clusters.reset_index()


def test_order_ties_explanations_and_input_immutability(tables):
    priority, roles, *_ = tables
    priority.loc[24, "priority_score"] = 0.9
    roles.loc[24, "priority_score"] = 0.9
    originals = [x.copy(deep=True) for x in (priority, roles)]
    top = run(priority, roles)
    assert list(top.columns) == TOP_COLUMNS
    assert top.gid.tolist() == [124] + list(range(100, 119))
    assert top["rank"].tolist() == list(range(1, 21))
    assert top.why.tolist() == priority.set_index("gid").loc[top.gid, "why"].tolist()
    shuffled = run(priority.sample(frac=1, random_state=7), roles.sample(frac=1, random_state=9))
    pd.testing.assert_frame_equal(top, shuffled)
    for original, current in zip(originals, (priority, roles)):
        pd.testing.assert_frame_equal(original, current)


def test_gid_relabeling_does_not_change_selection(tables):
    priority, roles, *_ = tables
    # Strictly increasing bijection also preserves the documented numeric tie-break.
    old = run(priority, roles)
    priority.gid = priority.gid * 17 + 900001
    roles.gid = roles.gid * 17 + 900001
    new = run(priority, roles)
    assert new.gid.tolist() == (old.gid * 17 + 900001).tolist()


@pytest.mark.parametrize("value", [np.nan, np.inf, -0.1, 1.01, "0.5"])
def test_bad_scores_rejected(tables, value):
    priority, roles, *_ = tables
    priority["priority_score"] = value
    with pytest.raises(ValueError, match="priority"):
        run(priority, roles)


@pytest.mark.parametrize("value", [None, "", "   ", 42])
def test_missing_explanations_rejected(tables, value):
    priority, roles, *_ = tables
    priority["why"] = value
    with pytest.raises(ValueError):
        run(priority, roles)


@pytest.mark.parametrize("count", [0, 19, 26, True, 20.5])
def test_invalid_top_size(tables, count):
    with pytest.raises(ValueError):
        run(*tables[:2], top_n=count)


def test_complete_roles_required_but_final_score_optional(tables):
    priority, roles, *_ = tables
    assert len(run(priority, roles[["gid", "role"]])) == 20
    for invalid in (roles.iloc[:-1], pd.concat([roles, roles.iloc[:1]]),
                    roles.assign(role="unknown"), roles.assign(priority_score=0.6)):
        with pytest.raises(ValueError):
            run(priority, invalid)
    with pytest.raises(ValueError):
        run(priority.assign(gid=priority.gid.astype(str)), roles)
    with pytest.raises(ValueError):
        run(pd.concat([priority, priority.iloc[:1]]), roles)


def test_audit_rejects_missing_cluster_assignment(tables):
    priority, roles, edges, nodes, nc, clusters = tables
    with pytest.raises(ValueError, match="nodes/node_clusters"):
        audit(run(priority, roles), priority, roles, edges, nodes, nc.iloc[:-1], clusters)


def test_external_flows_are_checked_per_cluster(tables):
    priority, roles, edges, nodes, nc, clusters = tables
    cid = nc.set_index("gid").cluster_id
    for column, endpoint in (("sum_kzt_in_external", "dst"),
                             ("sum_kzt_out_external", "src")):
        sums = edges.sum_kzt.groupby(edges[endpoint].map(cid)).sum()
        clusters[column] = clusters.cluster_id.map(sums).fillna(0)
    top = run(priority, roles)
    audit(top, *tables)
    clusters.loc[0, "sum_kzt_in_external"] += 10
    clusters.loc[1, "sum_kzt_in_external"] -= 10
    with pytest.raises(ValueError, match="sum_kzt_in_external"):
        audit(top, *tables)


def test_cluster_coverage_is_reported_without_forcing_quotas(tables):
    priority, roles, edges, nodes, nc, clusters = tables
    nc.cluster_id = (nc.gid >= 120).astype(int)
    roles.cluster_id = nc.cluster_id
    clusters = pd.DataFrame({"cluster_id": [0, 1], "n_nodes": [20, 5],
                             "n_seed": [10, 3], "sum_kzt_internal": [1900., 400.]})
    report = audit(run(priority, roles), priority, roles, edges, nodes, nc, clusters)
    assert report["missing_cluster_ids"] == [1]
    assert report["cluster_coverage"] == 0.5
    assert report["sum_kzt_total"] == 2400
    assert report["sum_kzt_internal"] + report["sum_kzt_between_clusters"] == 2400


@pytest.mark.parametrize("corruption", ["rank", "order", "why", "role", "score",
                                        "cluster", "inflow", "outflow", "summary",
                                        "size", "seed", "endpoint", "duplicate_edge"])
def test_audit_detects_inconsistency(tables, corruption):
    priority, roles, edges, nodes, nc, clusters = tables
    top = run(priority, roles)
    if corruption == "rank":
        top.loc[0, "rank"] = 8
    elif corruption == "order":
        top = top.iloc[::-1]
    elif corruption in ("why", "role"):
        top.loc[0, corruption] = "changed"
    elif corruption == "score":
        top.loc[0, "priority_score"] = 0.7
    elif corruption == "cluster":
        roles.loc[0, "cluster_id"] = 999
    elif corruption in ("inflow", "outflow"):
        column = "in_kzt" if corruption == "inflow" else "out_kzt"
        # Preserve global sum: per-node validation must still catch this.
        roles.loc[1, column] += 10
        roles.loc[2, column] -= 10
    elif corruption == "summary":
        clusters.loc[0, "sum_kzt_internal"] = 100
    elif corruption == "size":
        clusters.loc[0, "n_nodes"] += 1
    elif corruption == "seed":
        clusters.loc[0, "n_seed"] += 1
    elif corruption == "endpoint":
        edges.loc[0, "src"] = -999
    else:
        edges = pd.concat([edges, edges.iloc[:1]])
    with pytest.raises(ValueError):
        audit(top, priority, roles, edges, nodes, nc, clusters)


def test_real_graph_cluster_sums_and_csv_contract():
    from analytics.clustering import load_inputs, run as cluster

    data = Path(__file__).resolve().parents[1] / "data"
    if not (data / "edges.parquet").exists():
        pytest.skip("реальный датасет не установлен")
    edges, nodes = load_inputs(data)
    nc, clusters = cluster(edges, nodes)
    # Контрактные заглушки, НЕ результаты PAN-36 или классификатора ролей.
    priority = nodes[["gid"]].assign(priority_score=0.5, why="Тест контракта: сигнал=1")
    roles = nc.assign(role="peripheral", priority_score=0.5)
    for name, endpoint in (("in_kzt", "dst"), ("out_kzt", "src")):
        roles[name] = roles.gid.map(edges.groupby(endpoint).sum_kzt.sum()).fillna(0)
    top = run(priority, roles)
    restored = pd.read_csv(StringIO(top.to_csv(index=False)))
    report = audit(restored, priority, roles, edges, nodes, nc, clusters)
    assert report["n_nodes"] == 2248
    assert report["n_top"] == 20
    pd.testing.assert_frame_equal(top, restored)
