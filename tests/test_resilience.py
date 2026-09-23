"""PAN-44: ручные графы с известным результатом удаления и реальные контракты."""

import ast
import inspect
import re
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import pytest

from analytics.resilience import run


def inputs(gids, links, seed_gids=(), first=None):
    nodes = pd.DataFrame({"gid": pd.Series(gids, dtype="int64"),
                          "depth": [0 if g in seed_gids else 1 for g in gids],
                          "is_seed": [g in seed_gids for g in gids]})
    edges = pd.DataFrame(links, columns=["src", "dst", "sum_kzt"]).astype(
        {"src": "int64", "dst": "int64", "sum_kzt": "float64"})
    priority = nodes[["gid"]].assign(priority_score=[1.0 if g == first else 0.0 for g in gids])
    return edges, nodes, priority


def observation(result, n=1):
    s = result["scenarios"]
    return s[(s.strategy == "priority") & (s.n_removed == n)].iloc[0]


def test_bridge_removal_splits_survivors_and_directed_paths():
    data = inputs([1, 2, 3, 4, 5], [(1, 2, 10), (2, 3, 20), (3, 4, 30)], [1], 2)
    result = run(*data, steps=[1], random_runs=2)
    base = result["baseline"].iloc[0]
    assert base.weak_components == 2  # узел 5 уже был изолирован
    row = observation(result)
    assert row.survivor_pairs_before == 3  # пары 1,3,4, не включая удалённый 2
    assert row.survivor_pairs_after == 1  # только 3--4
    assert row.weak_pair_loss == pytest.approx(2 / 3)
    assert row.new_isolates == 1  # исходный изолят 5 не засчитывается как ущерб
    assert row.isolates == 2
    assert row.seed_reachable_before == 2
    assert row.seed_reachable_after == 0
    assert row.seed_reach_loss == 1
    assert row.removed_kzt == 30
    assert row.remaining_kzt == 30


def test_leaf_deletion_is_not_fragmentation():
    data = inputs([1, 2, 3], [(1, 2, 10), (2, 3, 20)], [1], 3)
    row = observation(run(*data, steps=[1], random_runs=1))
    assert row.weak_pair_loss == 0
    assert row.seed_reach_loss == 0
    assert row.new_isolates == 0
    assert row.largest_weak_size == 2  # размер уменьшился, но сеть не распалась


def test_comparison_can_refute_priority_advantage():
    # Приоритет намеренно выбирает лист звезды; degree выбирает центр.
    data = inputs([1, 2, 3, 4], [(1, 2, 10), (1, 3, 10), (1, 4, 10)], [1], 4)
    result = run(*data, steps=[0, 1], random_runs=3)
    comparison = result["comparisons"].query(
        "n_removed == 1 and reference == 'degree' and metric == 'weak_pair_loss'").iloc[0]
    assert comparison.priority == 0
    assert comparison.reference_mean == 1
    assert comparison.priority_minus_reference == -1
    assert comparison.reference_fraction_ge_priority == 1


def test_reachability_respects_direction_and_surviving_seeds():
    # Удаляем 2: по слабой связности остаётся путь 1--3--4, но 1 не достигнет 4.
    data = inputs([1, 2, 3, 4], [(1, 2, 1), (2, 4, 1), (3, 1, 1), (3, 4, 1)], [1], 2)
    row = observation(run(*data, steps=[1], random_runs=1))
    assert row.weak_pair_loss == 0
    assert row.seed_reach_loss == 1
    edges, nodes, priority = data
    priority.priority_score = [1., 0., 0., 0.]
    row = observation(run(edges, nodes, priority, steps=[1], random_runs=1))
    assert row.n_seed_remaining == 0
    assert np.isnan(row.seed_reach_loss)  # удаление единственного seed не «100% ущерб»


def test_reciprocal_edges_and_self_loops_counted_once_each():
    data = inputs([1, 2, 3], [(1, 2, 10), (2, 1, 20), (2, 2, 30), (2, 3, 40)], [1], 2)
    result = run(*data, steps=[1, 3], random_runs=1)
    assert result["baseline"].iloc[0].largest_strong_size == 2
    row = observation(result)
    assert row.removed_kzt == 100
    assert row.removed_kzt_share == 1
    assert row.new_isolates == 2
    empty = observation(result, 3)
    assert empty.n_remaining == empty.weak_components == empty.largest_strong_size == 0
    assert np.isnan(empty.weak_pair_loss)


def test_edgeless_graph_and_single_node_have_explicit_undefined_ratios():
    for gids in ([1], [1, 2, 3]):
        result = run(*inputs(gids, [], [1], 1), random_runs=1)
        for metric in ("weak_pair_loss", "seed_reach_loss", "removed_kzt_share"):
            assert result["scenarios"][metric].isna().all()
            assert result["summary"].query("metric == @metric").n_valid.eq(0).all()
        assert result["scenarios"].new_isolates.eq(0).all()


def test_determinism_input_immutability_and_stratified_prefixes():
    data = inputs(list(range(30)), [(i, i + 1, i + 1) for i in range(29)], [0, 3, 8], 15)
    edges, nodes, priority = data
    nodes.depth = nodes.gid % 5
    originals = [frame.copy(deep=True) for frame in data]
    a = run(*data, steps=[1, 5, 20], random_runs=4)
    b = run(*(f.sample(frac=1, random_state=7) for f in data), steps=[20, 5, 1], random_runs=4)
    for key in ("baseline", "scenarios", "summary", "comparisons", "removals"):
        pd.testing.assert_frame_equal(a[key], b[key])
    for old, new in zip(originals, data):
        pd.testing.assert_frame_equal(old, new)
    removed = a["removals"]
    info = nodes.set_index("gid")
    for (strategy, trial), group in removed.groupby(["strategy", "trial"]):
        assert group.gid.is_unique
        assert group["rank"].tolist() == list(range(1, 21))
        if strategy == "matched_random":
            for n in (1, 5, 20):
                expected = removed.query("strategy == 'priority'").head(n).gid
                actual = group.head(n).gid
                pd.testing.assert_series_equal(
                    info.loc[expected].groupby(["is_seed", "depth"]).size(),
                    info.loc[actual].groupby(["is_seed", "depth"]).size())
    from analytics.top_nodes import rank_candidates
    expected = rank_candidates(priority.assign(why="сигнал=1"))
    assert removed.query("strategy == 'priority'").gid.tolist() == expected.gid.tolist()


def test_arbitrary_gid_relabeling_preserves_priority_damage():
    data = inputs(list(range(8)), [(i, i + 1, 10) for i in range(7)], [0], 3)
    data[2].priority_score = np.linspace(0, 1, 8)
    a = run(*data, steps=[1, 3], random_runs=1)
    mapping = dict(zip(range(8), [903, 74, 610, 91, 122, 666, 844, 555]))
    edges, nodes, priority = (f.copy() for f in data)
    edges.src, edges.dst = edges.src.map(mapping), edges.dst.map(mapping)
    nodes.gid, priority.gid = nodes.gid.map(mapping), priority.gid.map(mapping)
    b = run(edges, nodes, priority, steps=[1, 3], random_runs=1)
    pd.testing.assert_frame_equal(a["scenarios"].query("strategy == 'priority'"),
                                  b["scenarios"].query("strategy == 'priority'"))


@pytest.mark.parametrize("fault", ["duplicate_gid", "missing_score", "unknown_edge", "duplicate_edge",
                                   "negative", "nan", "zero", "score_range", "score_bool", "float_gid"])
def test_invalid_data_rejected(fault):
    edges, nodes, priority = inputs([1, 2, 3], [(1, 2, 10), (2, 3, 20)], [1], 2)
    if fault == "duplicate_gid":
        nodes.loc[2, "gid"] = 1
    elif fault == "missing_score":
        priority = priority.iloc[:-1]
    elif fault == "unknown_edge":
        edges.loc[0, "src"] = 99
    elif fault == "duplicate_edge":
        edges = pd.concat([edges, edges.iloc[:1]])
    elif fault in ("negative", "nan", "zero"):
        edges.loc[0, "sum_kzt"] = {"negative": -1, "nan": np.nan, "zero": 0}[fault]
    elif fault == "score_range":
        priority.loc[0, "priority_score"] = 1.1
    elif fault == "score_bool":
        priority.priority_score = True
    else:
        nodes.gid = nodes.gid.astype(float)
    with pytest.raises(ValueError):
        run(edges, nodes, priority, random_runs=1)


@pytest.mark.parametrize("options", [{"steps": [4]}, {"steps": [-1]}, {"steps": [1.5]},
                                      {"steps": []}, {"random_runs": 0}, {"seed": -1}])
def test_invalid_experiment_settings(options):
    with pytest.raises(ValueError):
        run(*inputs([1, 2, 3], [(1, 2, 10)], [1], 2), **options)


def test_real_data_integrates_without_roles_and_has_no_gid_literals():
    import analytics.resilience as module
    from analytics.clustering import load_inputs, run as clustering
    from analytics.priority import run as scoring

    data_dir = Path(__file__).resolve().parents[1] / "data"
    if not (data_dir / "edges.parquet").exists():
        pytest.skip("нет датасета")
    edges, nodes = load_inputs(data_dir)
    nc, _ = clustering(edges, nodes)
    priority = scoring(edges, nodes, nc)
    result = run(edges, nodes, priority, steps=[1, 20], random_runs=2)
    assert result["baseline"].iloc[0].n_remaining == len(nodes) == 2248
    assert result["baseline"].iloc[0].isolates == 19
    scenarios = result["scenarios"]
    assert np.allclose(scenarios.remaining_kzt + scenarios.removed_kzt, edges.sum_kzt.sum())
    for metric in ("weak_pair_loss", "seed_reach_loss", "removed_kzt_share"):
        assert scenarios[metric].dropna().between(0, 1).all()
    assert scenarios.query("n_removed == 0").removed_kzt.eq(0).all()
    literals = set()
    for item in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(item, ast.Constant):
            if isinstance(item.value, int):
                literals.add(item.value)
            elif isinstance(item.value, str):
                literals.update(map(int, re.findall(r"\b[0-9]+\b", item.value)))
    assert not (literals & set(nodes.gid))
    assert nx.__version__ == "3.6.1"
