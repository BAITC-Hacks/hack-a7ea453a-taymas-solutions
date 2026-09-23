"""The public CI dataset must exercise the existing browser/evaluation flows."""

import pandas as pd
import pytest

from agent_tools import GraphStore, GraphTools
from agent_tools.evaluation import build_cases
from money_graph.pipeline import run
from scripts.ci_fixture import write_fixture


def test_ci_fixture_covers_demo_contract_without_private_data(tmp_path):
    data = tmp_path / "synthetic"
    write_fixture(data)
    tx = pd.read_parquet(data / "transactions.parquet")
    assert tx.duplicated().any()
    first, repeated = run(data), run(data)
    assert first.files == repeated.files
    tables = first.tables
    nodes, edges, top = (tables[name] for name in ("nodes_roles.csv", "edge_table.csv", "top_nodes.csv"))
    assert all(gid > 2**53 and len(str(gid)) == 18 for gid in nodes.gid)
    assert edges.n_tx.sum() == len(tx)
    assert edges.sum_kzt.sum() == tx.sum_kzt.sum()
    primary = nodes.set_index("gid").loc[top.iloc[0].gid]
    assert primary.role == "coordinator"
    assert primary.n_payers >= 2
    assert (nodes.role == "terminal").any()
    assert (nodes.role == "transit").any()
    assert ((nodes.depth == 4) & (nodes.role != "terminal")).any()
    assert (nodes.external_inflow_suspected & ~nodes.is_seed & (nodes.depth != 4)).any()
    assert (nodes.is_seed & (nodes.in_deg == 0) & (nodes.out_deg == 0)).any()
    store = GraphStore(nodes, edges, tables["clusters.csv"], top)
    assert {case.id for case in build_cases(GraphTools(store))} == {
        "priority", "common_collector", "trace", "depth4", "empty",
    }


def test_ci_fixture_refuses_to_overwrite_existing_directory(tmp_path):
    existing = tmp_path / "organizer-data"
    existing.mkdir()
    sentinel = existing / "nodes.parquet"
    sentinel.write_bytes(b"do not replace")
    with pytest.raises(FileExistsError):
        write_fixture(existing)
    assert sentinel.read_bytes() == b"do not replace"
    assert list(existing.iterdir()) == [sentinel]
