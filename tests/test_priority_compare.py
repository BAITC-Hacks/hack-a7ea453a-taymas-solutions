"""PAN-57: разложение score, официальный порядок и устойчивость пары."""

import json

import numpy as np
import pandas as pd
import pytest

from agent_tools import GraphStore, GraphTools
from analytics.priority import WEIGHTS, weight_sensitivity
from analytics.priority_compare import (CONTRIB, SCORE_TOLERANCE, compare, pair_stability)
from tests._mini import DATA, HAS_DATA, build_outputs


def synthetic(rows):
    """gid + вклады collect/fanout; остальные признаки — явная синтетика."""
    pr = pd.DataFrame(rows, columns=["gid", "contrib_collect", "contrib_fanout"])
    for c in CONTRIB[2:]:
        pr[c] = 0.0
    pr["boundary_factor"] = 1.0
    pr["priority_score"] = pr[CONTRIB].sum(axis=1).round(4)
    return pr.assign(role="peripheral", role_score=0.0, depth=1, is_seed=False,
                     in_kzt=10_000.0, out_kzt=10_000.0, in_deg=1, out_deg=1,
                     n_payers=1, n_receivers=1, n_seed_payers=0, n_seed_receivers=0,
                     out_observable=True, external_inflow_suspected=False)


def test_stable_pair_and_top_k_use_all_nodes():
    pr = synthetic([(1, .2, .1), (2, .1, .05), (3, .25, .15)])
    r = pair_stability(pr, 1, 2, top_k=2)
    assert r["a"] == {"gid": "1", "baseline_rank": 2, "rank_min": 2, "rank_max": 2,
                      "top_k_count": 1000, "top_k_frequency": 1.0}
    assert r["b"]["rank_min"] == r["b"]["rank_max"] == 3
    assert r["b"]["top_k_frequency"] == 0
    assert r["a_above_b_count"] == 1000


def test_pair_changes_order_and_reverse_is_complement():
    pr = synthetic([(2, .15, 0), (10, 0, .15)])
    r = pair_stability(pr, "2", "10", top_k=1)
    reverse = pair_stability(pr, 10, 2, top_k=1)
    assert .3 < r["a_above_b_frequency"] < .7
    assert r["a"]["rank_min"] == r["b"]["rank_min"] == 1
    assert r["a"]["rank_max"] == r["b"]["rank_max"] == 2
    assert r["a"]["top_k_count"] == r["a_above_b_count"]
    assert r["a_above_b_count"] + reverse["a_above_b_count"] == 1000
    assert r["a"] == reverse["b"]
    assert r["baseline"]["a_above_b"]  # numeric gid 2 < 10


def test_deterministic_without_mutation_or_global_rng():
    pr = synthetic([(10, 0, .15), (2, .15, 0), (30, .1, .1)])
    before = pr.copy(deep=True)
    first = pair_stability(pr, 2, 10, seed=8)
    shuffled = pr.sample(frac=1, random_state=19).iloc[:, ::-1].set_index("gid", drop=False)
    np.random.seed(54)
    assert pair_stability(shuffled, 2, 10, seed=8) == first
    assert compare(shuffled, 2, 10) == compare(pr, 2, 10)
    assert pair_stability(pr, 2, 10, seed=9)["a_above_b_count"] != first["a_above_b_count"]
    pd.testing.assert_frame_equal(pr, before)
    json.dumps(first, allow_nan=False)
    json.dumps(compare(pr, 2, 10), allow_nan=False)


def test_original_weights_use_official_scores_despite_rounded_contributions():
    # Reconstructing rounded contributions would put 10 above 2 and 30 above both.
    pr = synthetic([(10, .2001, 0), (2, .1999, 0), (30, .2002, 0)])
    pr["priority_score"] = [.2, .2, .1999]
    pr["gid"] = pr.gid.astype(str)
    for a, b in [(2, 10), (10, 30), (2, 30)]:
        r = pair_stability(pr, a, b, spread=0, n_runs=7, top_k=1)
        c = compare(pr, a, b)
        assert r["a_above_b_frequency"] == 1
        assert r["a"]["baseline_rank"] == c["a"]["rank"]
        for side in ("a", "b"):
            assert r[side]["rank_min"] == r[side]["rank_max"] == c[side]["rank"]
    assert compare(pr, 2, 10)["order_reason"] == "gid_ascending"


def test_ties_use_gid_in_every_random_scenario():
    pr = synthetic([(10, .1, .1), (2, .1, .1)])
    r = pair_stability(pr, 2, 10, top_k=1)
    assert r["a_above_b_count"] == 1000
    assert r["a"]["top_k_frequency"] == 1


def test_score_decomposition_boundary_and_numeric_reasons():
    pr = synthetic([(1, .25, .15), (2, .2, .1)])
    pr.loc[0, ["boundary_factor", "priority_score"]] = [.8, .32]
    r = compare(pr, 1, 2)
    for side in ("a", "b"):
        n = r[side]
        assert n["score_from_contrib"] == pytest.approx(sum(n[c] for c in CONTRIB) * n["boundary_factor"])
        assert abs(n["rounding_residual"]) <= SCORE_TOLERANCE
    d = r["delta"]
    assert sum(d[c] for c in CONTRIB) + d["boundary_adjustment"] + d["rounding_residual"] == pytest.approx(d["priority_score"])
    assert r["main_reasons"][0]["component"] == "boundary_adjustment"
    assert r["main_reasons"][0]["delta"] == pytest.approx(-.08)
    assert "-0.080000" in r["main_reasons"][0]["message"]


@pytest.mark.parametrize("fn", [compare, pair_stability])
@pytest.mark.parametrize("a,b,match", [(1, 1, "один и тот же"), (1, "01", "один и тот же"),
                                      (1, 999, "неизвестный gid: 999"),
                                      (1.0, 2, "float"), (True, 2, "gid"),
                                      (np.bool_(True), 2, "gid")])
def test_invalid_gids(fn, a, b, match):
    with pytest.raises(ValueError, match=match):
        fn(synthetic([(1, .2, 0), (2, .1, 0)]), a, b)


@pytest.mark.parametrize("fn", [compare, pair_stability])
@pytest.mark.parametrize("column", ["priority_score", "contrib_flow", "boundary_factor"])
def test_missing_diagnostics(fn, column):
    with pytest.raises(ValueError, match=column):
        fn(synthetic([(1, .2, 0), (2, .1, 0)]).drop(columns=column), 1, 2)


def test_missing_context_and_duplicate_gids():
    pr = synthetic([(1, .2, 0), (2, .1, 0)])
    with pytest.raises(ValueError, match="role"):
        compare(pr.drop(columns="role"), 1, 2)
    with pytest.raises(ValueError, match="уникальны"):
        pair_stability(pd.concat([pr, pr]), 1, 2)
    with pytest.raises(ValueError, match="bool"):
        compare(pr.assign(is_seed="False"), 1, 2)


@pytest.mark.parametrize("column,value,match", [
    ("contrib_flow", np.nan, "пустые"), ("contrib_flow", np.inf, "конечные"),
    ("contrib_flow", -.1, "неотрицательные"), ("contrib_flow", .5, "превышает"),
    ("priority_score", .9, "не воспроизводится"), ("boundary_factor", 0, "множитель"),
])
def test_invalid_diagnostic_values(column, value, match):
    pr = synthetic([(1, .2, 0), (2, .1, 0)])
    with pytest.raises(ValueError, match=match):
        pair_stability(pr.assign(**{column: value}), 1, 2)


@pytest.mark.parametrize("kwargs", [{"top_k": 0}, {"top_k": True}, {"n_runs": 0}, {"n_runs": 1.5},
                                   {"seed": -1}, {"seed": False}, {"spread": -1}, {"spread": 1.1},
                                   {"spread": np.nan}, {"spread": np.inf}, {"spread": True}])
def test_invalid_experiment_parameters(kwargs):
    with pytest.raises(ValueError, match=next(iter(kwargs))):
        pair_stability(synthetic([(1, .2, 0), (2, .1, 0)]), 1, 2, **kwargs)


def test_top_k_larger_than_population():
    r = pair_stability(synthetic([(1, .2, 0), (2, .1, 0)]), 1, 2, n_runs=1, spread=1)
    assert r["parameters"]["top_k"] == 20
    assert r["parameters"]["effective_top_k"] == 2
    assert r["a"]["top_k_frequency"] == r["b"]["top_k_frequency"] == 1


def test_version_tracks_used_data():
    pr = synthetic([(1, .2, 0), (2, .1, 0)])
    original = compare(pr, 1, 2)
    assert compare(pr.assign(role="transit"), 1, 2)["data_version"] != original["data_version"]
    assert compare(pr.assign(unused="ignored"), 1, 2)["data_version"] == original["data_version"]
    changed = pr.copy()
    changed.loc[0, ["contrib_collect", "priority_score"]] = [.21, .21]
    assert pair_stability(changed, 1, 2)["data_version"] != pair_stability(pr, 1, 2)["data_version"]


def legacy_sensitivity(pr, top, spread, n_runs, seed):
    """Frozen pre-refactor algorithm: verify backward compatibility, including RNG."""
    keys = list(WEIGHTS)
    comp = np.column_stack([pr[f"contrib_{k}"] / WEIGHTS[k] for k in keys])
    factor = pr.boundary_factor.to_numpy()
    w0 = np.array([WEIGHTS[k] for k in keys])
    gids = pr.gid.to_numpy()

    def top_gids(score):
        return set(gids[np.lexsort((gids, -score))[:top]])

    base = top_gids(comp @ w0 * factor)
    rng = np.random.default_rng(seed)
    overlap = np.array([len(base & top_gids(comp @ (w0 * rng.uniform(1-spread, 1+spread, len(keys))) * factor)) / top
                        for _ in range(n_runs)])
    drop = {k: len(base & top_gids(comp @ np.where(np.arange(len(keys)) == i, 0, w0) * factor))
            for i, k in enumerate(keys)}
    return {"random_mean": float(overlap.mean()), "random_p05": float(np.quantile(overlap, .05)),
            "random_min": float(overlap.min()), "drop": drop}


@pytest.mark.parametrize("spread,seed", [(0, 0), (.5, 0), (.8, 19)])
def test_weight_sensitivity_results_unchanged(spread, seed):
    pr = synthetic([(10, 0, .15), (2, .15, 0), (30, .1, .1)])
    assert weight_sensitivity(pr, 2, spread, 31, seed) == legacy_sensitivity(pr, 2, spread, 31, seed)


@pytest.fixture(scope="module")
def graph_tools(tmp_path_factory):
    return GraphTools(GraphStore.from_dir(build_outputs(tmp_path_factory.mktemp("compare"))))


def test_compare_agrees_with_graph_tools_and_reuses_warnings(graph_tools):
    pr = graph_tools.store.nodes
    for a, b in [(1, 41), (11, 22)]:
        r = compare(pr, a, b)
        graph = {str(n["gid"]): n for n in graph_tools.compare_nodes([a, b])["nodes"]}
        for side in ("a", "b"):
            n = r[side]
            for key in ("role", "role_score", "priority_score", "in_kzt", "out_kzt", "n_payers", "n_receivers"):
                assert n[key] == graph[n["gid"]][key]
            assert [w["code"] for w in n["warnings"]] == [w["code"] for w in graph[n["gid"]]["warnings"]]
    assert "seed_inflow_underestimated" in {w["code"] for w in compare(pr, 1, 41)["a"]["warnings"]}
    assert "depth4_outflow_unobserved" in {w["code"] for w in compare(pr, 1, 41)["b"]["warnings"]}


def test_incomplete_inflow_warning_has_numbers():
    pr = synthetic([(1, .2, 0), (2, .1, 0)])
    pr.loc[0, ["out_kzt", "external_inflow_suspected"]] = [20_000.0, True]
    warning = compare(pr, 1, 2)["a"]["warnings"][0]
    assert warning["code"] == "external_inflow"
    assert "вход неполный" in warning["message"] and "20000" in warning["message"]


@pytest.fixture(scope="module")
def real_result():
    if not HAS_DATA:
        pytest.skip("нет data/edges.parquet")
    from money_graph.pipeline import run
    return run(DATA)


def test_real_scores_official_order_and_legacy_sensitivity(real_result):
    pr = real_result.tables["nodes_roles.csv"]
    rebuilt = pr[CONTRIB].sum(axis=1) * pr.boundary_factor
    assert (rebuilt - pr.priority_score).abs().max() <= SCORE_TOLERANCE
    official = pr.sort_values(["priority_score", "gid"], ascending=[False, True])
    # Full order including ties, not only the published top-30.
    from analytics.priority_compare import _ranks
    ranks = _ranks(pr, pr.priority_score.to_numpy())
    assert pr.gid.to_numpy()[np.argsort(ranks)].tolist() == official.gid.tolist()
    for _, row in real_result.tables["top_nodes.csv"].iterrows():
        i = pr.index[pr.gid == row.gid][0]
        assert ranks[i] == row["rank"]
    a, b = official.gid.iloc[:2]
    assert pair_stability(pr, a, b, spread=0)["a"]["rank_min"] == 1
    assert weight_sensitivity(pr) == {"random_mean": .8498, "random_p05": .7, "random_min": .5,
                                     "drop": {"collect": 13, "fanout": 14, "flow": 16,
                                              "seed": 17, "bridge": 16, "volume": 18}}
    assert weight_sensitivity(pr, 20, .5, 23, 7) == legacy_sensitivity(pr, 20, .5, 23, 7)


@pytest.mark.parametrize("a,b,frequency,ranks,scores", [
    # Real adjacent top-30 pairs found by scanning official ranks; docs/priority_compare.md.
    ("100000003684369100", "100000008603629100", 1.0, (2, 3, 1, 3, 2, 16), (.6970, .6091)),
    ("100000008165763100", "100000003684369100", .682, (1, 2, 1, 3, 1, 3), (.7226, .6970)),
])
def test_documented_real_examples(real_result, a, b, frequency, ranks, scores):
    pr = real_result.tables["nodes_roles.csv"]
    r = pair_stability(pr, a, b)
    c = compare(pr, a, b)
    assert r["a_above_b_frequency"] == frequency
    assert r["a"]["top_k_count"] == r["b"]["top_k_count"] == 1000
    assert (r["a"]["baseline_rank"], r["b"]["baseline_rank"], r["a"]["rank_min"], r["a"]["rank_max"],
            r["b"]["rank_min"], r["b"]["rank_max"]) == ranks
    assert (c["a"]["priority_score"], c["b"]["priority_score"]) == scores
    shuffled = pr.sample(frac=1, random_state=57)
    assert pair_stability(shuffled, a, b) == r
    assert compare(shuffled, a, b) == c
    json.dumps({"comparison": c, "stability": r}, allow_nan=False)
