"""Проверки priority_score (PAN-36): синтетический граф + реальный датасет."""

from pathlib import Path

import pandas as pd
import pytest

from analytics.priority import BOUNDARY_FACTOR, WEIGHTS, run, weight_sensitivity

DATA = Path(__file__).resolve().parents[1] / "data"
CONTRIB = [f"contrib_{k}" for k in WEIGHTS]


# ---------------------------------------------------------------- синтетика

def toy():
    """1, 2 — seed; 3 — seed без переводов. 10 собирает от 4 плательщиков
    и оставляет себе 93%; 20 и 30 передают дальше ровно полученное;
    40 — 4-е колено, обход на нём остановился. 11 и 12 симметричны."""
    edges = pd.DataFrame(
        [(1, 11, 100_000, 1, 1), (1, 12, 100_000, 1, 1), (1, 10, 50_000, 1, 1),
         (2, 10, 50_000, 2, 1), (11, 10, 100_000, 1, 2), (12, 10, 100_000, 1, 2),
         (10, 20, 20_000, 1, 2), (20, 30, 20_000, 1, 3), (30, 40, 20_000, 1, 4)],
        columns=["src", "dst", "sum_kzt", "n_tx", "depth"])
    nodes = pd.DataFrame({"gid": [1, 2, 3, 10, 11, 12, 20, 30, 40],
                          "depth": [0, 0, 0, 1, 1, 1, 2, 3, 4]})
    nodes["is_seed"] = nodes.depth == 0
    clusters = pd.DataFrame({"gid": [1, 11, 12, 10, 2, 20, 30, 40, 3],
                             "cluster_id": [1, 1, 1, 1, 2, 3, 3, 3, 4]})
    return edges, nodes, clusters


@pytest.fixture(scope="module")
def toy_result():
    return run(*toy()).set_index("gid")


def test_toy_contract(toy_result):
    _, nodes, _ = toy()
    pr = toy_result.reset_index()
    assert list(pr.columns[:3]) == ["gid", "priority_score", "why"]
    assert sorted(pr.gid) == sorted(nodes.gid)
    assert pr.priority_score.between(0, 1).all()
    assert (pr.why.str.len() > 0).all()


def test_toy_sorted_with_gid_tiebreak(toy_result):
    pr = toy_result.reset_index()
    expected = pr.sort_values(["priority_score", "gid"], ascending=[False, True]).reset_index(drop=True)
    pd.testing.assert_frame_equal(pr, expected)
    assert toy_result.loc[11].priority_score == toy_result.loc[12].priority_score
    assert list(pr.gid).index(11) < list(pr.gid).index(12)


def test_toy_score_is_sum_of_contributions(toy_result):
    rebuilt = toy_result[CONTRIB].sum(axis=1) * toy_result.boundary_factor
    assert (rebuilt - toy_result.priority_score).abs().max() < 1e-3


def test_toy_isolated_seed_scores_zero(toy_result):
    assert toy_result.loc[3].priority_score == 0
    assert "нет переводов" in toy_result.loc[3].why


def test_toy_consolidation(toy_result):
    r = toy_result.loc[10]
    assert r.contrib_collect == WEIGHTS["collect"]      # максимум плательщиков в графе
    # оставил 280 из 300 тыс. при максимальном сборе → 0.15 × 0.933
    assert r.contrib_flow == pytest.approx(WEIGHTS["flow"] * 280 / 300, abs=1e-4)
    assert "4 плательщ." in r.why and "2 seed" in r.why
    assert toy_result.priority_score.idxmax() == 10


def test_toy_transit(toy_result):
    assert toy_result.loc[20].contrib_flow == WEIGHTS["flow"]
    assert "транзит: отправил 100%" in toy_result.loc[20].why


def test_toy_boundary_node(toy_result):
    r = toy_result.loc[40]
    assert r.boundary_factor == BOUNDARY_FACTOR
    assert r.contrib_flow == 0 and r.contrib_fanout == 0
    assert "4-е колено" in r.why


def test_toy_seed_flow_not_scored(toy_result):
    # вход seed занижен выгрузкой — out/in у него не интерпретируется
    assert toy_result.loc[1].contrib_flow == 0
    assert toy_result.loc[2].contrib_flow == 0


def test_toy_does_not_depend_on_gid_values(toy_result):
    edges, nodes, clusters = toy()
    relabel = {g: 9_000_000 - 7 * g for g in nodes.gid}
    moved = run(edges.assign(src=edges.src.map(relabel), dst=edges.dst.map(relabel)),
                nodes.assign(gid=nodes.gid.map(relabel)),
                clusters.assign(gid=clusters.gid.map(relabel))).set_index("gid")
    back = moved.rename(index={v: k for k, v in relabel.items()})
    cols = ["priority_score"] + CONTRIB + ["boundary_factor"]
    pd.testing.assert_frame_equal(back.loc[toy_result.index, cols], toy_result[cols])


def test_toy_more_payers_raise_collect():
    edges, nodes, clusters = toy()
    before = run(edges, nodes, clusters).set_index("gid").loc[20].contrib_collect
    edges = pd.concat([edges, pd.DataFrame([(11, 20, 5_000, 1, 2)], columns=edges.columns)])
    after = run(edges, nodes, clusters).set_index("gid").loc[20].contrib_collect
    assert after > before


# ---------------------------------------------------------------- датасет

real = pytest.mark.skipif(not (DATA / "edges.parquet").exists(),
                          reason="нет data/edges.parquet — распакуйте архив данных в ./data")


@pytest.fixture(scope="module")
def inputs():
    from analytics.clustering import load_inputs, run as run_clustering
    edges, nodes = load_inputs(DATA)
    node_clusters, _ = run_clustering(edges, nodes)
    return edges, nodes, node_clusters


@pytest.fixture(scope="module")
def result(inputs):
    return run(*inputs)


@real
def test_every_node_scored(inputs, result):
    _, nodes, _ = inputs
    assert len(result) == len(nodes) == 2248
    assert result.gid.is_unique and set(result.gid) == set(nodes.gid)
    assert result.priority_score.between(0, 1).all()
    assert result[["gid", "priority_score", "why"]].notna().all().all()


@real
def test_why_has_numbers(inputs, result):
    edges, _, _ = inputs
    active = result.gid.isin(set(edges.src) | set(edges.dst))
    assert result.why[active].str.contains(r"\d").all()
    assert result.why.str.len().max() <= 200


@real
def test_boundary_nodes_get_no_outgoing_credit(inputs, result):
    _, nodes, _ = inputs
    truncated = result[result.boundary_factor < 1]
    assert len(truncated) == 444 == (nodes.depth == 4).sum()
    assert (truncated[["contrib_fanout", "contrib_flow"]] == 0).all().all()


@real
def test_deterministic(inputs, result):
    pd.testing.assert_frame_equal(result, run(*inputs))


@real
def test_top20_stable_under_weight_changes(result):
    s = weight_sensitivity(result)
    assert s["random_mean"] >= 0.8
    assert min(s["drop"].values()) >= 12
