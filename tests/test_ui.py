import pandas as pd
import pytest

from money_graph.ui import load_data, parse_gid, select_view


def sample_tables():
    roles = pd.DataFrame(
        {
            "gid": [1, 2, 3, 4],
            "role": ["coordinator", "transit", "terminal", "peripheral"],
            "role_score": [0.9, 0.8, 0.7, 0.3],
            "cluster_id": [1, 1, 1, 2],
            "priority_score": [0.9, 0.8, 0.6, 0.2],
            "evidence": ["x1", "x2", "x3", "x4"],
            "depth": [0, 1, 2, 4],
            "is_seed": [True, False, False, False],
        }
    )
    edges = pd.DataFrame(
        {
            "src": [1, 2, 2, 4],
            "dst": [2, 3, 1, 3],
            "sum_kzt": [100.0, 80.0, 10.0, 5.0],
            "n_tx": [1, 2, 1, 1],
        }
    )
    return edges, roles


def test_parse_gid_is_safe():
    assert parse_gid(" 42 ") == 42
    assert parse_gid(7) == 7
    assert parse_gid("") is None
    assert parse_gid("abc") is None


def test_neighborhood_keeps_directional_one_hop_edges():
    edges, roles = sample_tables()
    nodes, view_edges, selected, truncated = select_view(edges, roles, query="2", mode="Соседи узла")
    assert selected == 2
    assert set(nodes.gid) == {1, 2, 3}
    assert set(map(tuple, view_edges[["src", "dst"]].to_numpy())) == {(1, 2), (2, 3), (2, 1)}
    assert truncated is False


def test_filters_and_max_nodes_are_deterministic():
    edges, roles = sample_tables()
    nodes, view_edges, selected, truncated = select_view(
        edges, roles, selected_roles=["transit", "terminal", "peripheral"], max_nodes=2, mode="Сеть по фильтрам"
    )
    assert selected is None
    assert list(nodes.gid) == [2, 3]
    assert set(view_edges["src"]).issubset({2, 3})
    assert set(view_edges["dst"]).issubset({2, 3})
    assert truncated is True


def test_load_data_requires_generated_roles(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "out").mkdir()
    with pytest.raises(FileNotFoundError):
        load_data(tmp_path / "data", tmp_path / "out")
