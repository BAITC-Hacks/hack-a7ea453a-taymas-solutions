"""Проверки приёмки PAN-35 на реальном датасете."""

from pathlib import Path

import pandas as pd
import pytest

from analytics.clustering import load_inputs, run

DATA = Path(__file__).resolve().parents[1] / "data"

# данные в репозиторий не коммитятся — без распакованного архива тесты пропускаются
pytestmark = pytest.mark.skipif(not (DATA / "edges.parquet").exists(),
                                reason="нет data/edges.parquet — распакуйте архив данных в ./data")


@pytest.fixture(scope="module")
def inputs():
    return load_inputs(DATA)


@pytest.fixture(scope="module")
def result(inputs):
    return run(*inputs)


def test_every_node_has_exactly_one_cluster(inputs, result):
    _, nodes = inputs
    node_clusters, clusters = result
    assert len(node_clusters) == len(nodes) == 2248
    assert node_clusters.gid.is_unique
    assert set(node_clusters.gid) == set(nodes.gid)
    assert set(node_clusters.cluster_id) == set(clusters.cluster_id)


def test_cluster_summary_matches_assignment(inputs, result):
    edges, nodes = inputs
    node_clusters, clusters = result
    assert clusters.n_nodes.sum() == len(nodes)
    assert clusters.n_seed.sum() == nodes.is_seed.sum() == 81
    sizes = node_clusters.cluster_id.value_counts()
    assert (clusters.set_index("cluster_id").n_nodes == sizes.sort_index()).all()

    # внутренний оборот = рёбра, у которых оба конца в одном кластере
    cid = node_clusters.set_index("gid").cluster_id
    same = edges.src.map(cid) == edges.dst.map(cid)
    assert clusters.sum_kzt_internal.sum() == pytest.approx(edges.sum_kzt[same].sum(), abs=1)


def test_required_columns_filled(result):
    _, clusters = result
    required = ["cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"]
    assert list(clusters.columns[:6]) == required
    assert clusters[required].notna().all().all()
    assert (clusters.hypothesis.str.len() > 0).all()


def test_clusters_do_not_cross_components(result):
    node_clusters, _ = result
    assert (node_clusters.groupby("cluster_id").component_id.nunique() == 1).all()


def test_deterministic(inputs, result):
    node_clusters, clusters = result
    again_nodes, again_clusters = run(*inputs)
    pd.testing.assert_frame_equal(node_clusters, again_nodes)
    pd.testing.assert_frame_equal(clusters, again_clusters)
