"""PAN-43: пайплайн money_graph строит priority_score и top_nodes.csv через analytics.

Проверяется, что выгрузки совпадают с тем, что модули PAN-36/37 возвращают сами,
и что скор, роль и why в nodes_roles.csv и top_nodes.csv берутся из одного источника.
"""

import pandas as pd
import pytest

from analytics.clustering import run as run_clustering
from analytics.priority import run as run_priority
from analytics.top_nodes import run as run_top_nodes
from money_graph.outputs import OutputSchemaError, validate_outputs
from money_graph.pipeline import run
from money_graph.ranking import TOP_N, audit_top_nodes

from tests._mini import DATA, HAS_DATA, mini_frames, write_mini_parquet


@pytest.fixture(scope="module")
def mini_run(tmp_path_factory):
    return run(write_mini_parquet(tmp_path_factory.mktemp("mini") / "data"))


def _standalone(edges, nodes):
    """Что analytics.priority возвращает без пайплайна, на тех же входах."""
    node_clusters, _ = run_clustering(edges, nodes)
    return run_priority(edges, nodes, node_clusters)


def _assert_matches_analytics(result, edges, nodes, top_n):
    nr, top = result.tables["nodes_roles.csv"], result.tables["top_nodes.csv"]
    pr = _standalone(edges, nodes)
    got = nr.set_index("gid").loc[pr.gid]
    assert (got.priority_score.to_numpy() == pr.priority_score.to_numpy()).all()
    assert (got.priority_why.to_numpy() == pr.why.to_numpy()).all()
    for col in [c for c in pr.columns if c.startswith("contrib_")] + ["boundary_factor"]:
        assert (got[col].to_numpy() == pr[col].to_numpy()).all(), col
    expected = run_top_nodes(pr, nr[["gid", "role", "priority_score"]], top_n=top_n)
    pd.testing.assert_frame_equal(top, expected)


def test_mini_outputs_come_from_analytics(mini_run):
    edges, nodes, _ = mini_frames()
    # граф меньше 20 узлов: топ — все узлы
    _assert_matches_analytics(mini_run, edges, nodes, top_n=len(nodes))


def test_priority_columns_follow_required_block(mini_run):
    cols = list(mini_run.tables["nodes_roles.csv"].columns)
    assert cols[6:9] == ["role_rule", "priority_why", "contrib_collect"]


def test_top_why_mismatch_is_caught(mini_run):
    t = mini_run.tables
    top = t["top_nodes.csv"].copy()
    top.loc[0, "why"] = "другое объяснение: 1 плательщик"
    with pytest.raises(OutputSchemaError, match="why совпадает с priority_why"):
        validate_outputs(t["nodes_roles.csv"], mini_frames()[1], t["clusters.csv"], top)


def test_audit_catches_top_not_rebuilt_from_nodes_roles(mini_run):
    t = mini_run.tables
    edges, nodes, _ = mini_frames()
    nr = t["nodes_roles.csv"].copy()
    nr.loc[nr.gid == t["top_nodes.csv"].gid.iloc[-1], "priority_score"] = 1.0
    with pytest.raises(OutputSchemaError, match="аудит top_nodes"):
        audit_top_nodes(nr, t["top_nodes.csv"], edges, nodes, t["clusters.csv"])


@pytest.mark.skipif(not HAS_DATA, reason="нет data/edges.parquet — распакуйте архив данных в ./data")
def test_real_dataset_outputs_come_from_analytics():
    result = run(DATA)
    edges, nodes = pd.read_parquet(DATA / "edges.parquet"), pd.read_parquet(DATA / "nodes.parquet")
    _assert_matches_analytics(result, edges, nodes, top_n=TOP_N)
    assert any(c.startswith("top_nodes: аудит PAN-37") for c in result.checks)
