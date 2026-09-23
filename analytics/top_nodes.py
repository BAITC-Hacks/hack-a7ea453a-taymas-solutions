"""PAN-37: строгий top-N и проверки согласованности аналитических таблиц.

Функции принимают DataFrame, не вычисляют роли/скоринг и не пишут на диск.
"""

from numbers import Integral

import numpy as np
import pandas as pd
from pandas.api.types import is_integer_dtype, is_numeric_dtype

ROLES = frozenset({"consolidator", "transit", "distributor", "terminal",
                   "coordinator", "peripheral"})
TOP_COLUMNS = ["rank", "gid", "role", "priority_score", "why"]


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _columns(frame, required, name):
    _require(frame.columns.is_unique, f"{name}: повторяющиеся колонки")
    missing = set(required) - set(frame.columns)
    _require(not missing, f"{name}: отсутствуют колонки {sorted(missing)}")
    _require(not frame[list(required)].isna().any().any(), f"{name}: пустые значения")


def _key(frame, column, name):
    _columns(frame, [column], name)
    _require(is_integer_dtype(frame[column].dtype), f"{name}.{column}: нужен целый тип")
    _require(frame[column].is_unique, f"{name}.{column}: дубликаты")


def _numbers(values, name, upper=None):
    _require(is_numeric_dtype(values.dtype), f"{name}: нужен числовой тип")
    _require(not values.isna().any(), f"{name}: пустые значения")
    _require(bool(np.isfinite(values).all() and (values >= 0).all()),
             f"{name}: нужны конечные неотрицательные числа")
    if upper is not None:
        _require(bool((values <= upper).all()), f"{name}: значения выше {upper}")


def _same_ids(left, right, name):
    _require(set(left) == set(right), f"{name}: набор идентификаторов не совпадает")


def _close(actual, expected, name):
    # Абсолютный допуск на округление до тиынов; относительного допуска нет.
    _require(bool(np.isclose(actual, expected, rtol=0, atol=0.011).all()),
             f"{name}: значения не совпадают")


def run(priority: pd.DataFrame, nodes_roles: pd.DataFrame,
        top_n: int = 20) -> pd.DataFrame:
    """Вернуть rank/gid/role/priority_score/why; приоритет ↓, gid ↑.

    nodes_roles может быть промежуточной таблицей gid/role. Если в ней уже
    есть priority_score, он обязан совпадать с PAN-36 (без округления).
    Входы должны покрывать один и тот же полный набор gid.
    """
    _require(isinstance(top_n, Integral) and not isinstance(top_n, bool)
             and top_n >= 20, "top_n: нужно целое число >= 20")
    _columns(priority, ["gid", "priority_score", "why"], "priority")
    _columns(nodes_roles, ["gid", "role"], "nodes_roles")
    _key(priority, "gid", "priority")
    _key(nodes_roles, "gid", "nodes_roles")
    _same_ids(priority.gid, nodes_roles.gid, "priority/nodes_roles")
    _require(len(priority) >= top_n, "недостаточно узлов для top_n")
    _numbers(priority.priority_score, "priority.priority_score", upper=1)
    _require(bool(nodes_roles.role.isin(ROLES).all()), "nodes_roles: неизвестная роль")
    _require(bool(priority.why.map(lambda x: isinstance(x, str) and bool(x.strip())).all()),
             "priority.why: нужны непустые текстовые объяснения")
    if "priority_score" in nodes_roles:
        _columns(nodes_roles, ["priority_score"], "nodes_roles")
        _numbers(nodes_roles.priority_score, "nodes_roles.priority_score", upper=1)
        expected = priority.set_index("gid").priority_score
        _require(bool((nodes_roles.priority_score.to_numpy()
                       == nodes_roles.gid.map(expected).to_numpy()).all()),
                 "nodes_roles.priority_score: расходится с priority")
    result = priority[["gid", "priority_score", "why"]].merge(
        nodes_roles[["gid", "role"]], on="gid", validate="one_to_one")
    result = result.sort_values(["priority_score", "gid"], ascending=[False, True])
    result = result.head(top_n).reset_index(drop=True)
    result.insert(0, "rank", np.arange(1, len(result) + 1))
    return result[TOP_COLUMNS]


def audit(top_nodes, priority, nodes_roles, edges, nodes, node_clusters, clusters):
    """Проверить топ, покрытие и суммы; вернуть отчёт (без записи файлов).

    nodes_roles здесь уже итоговая таблица: gid, role, priority_score,
    cluster_id, in_kzt, out_kzt обязательны. Проверки остальных полей ролей
    остаются в PAN-34. Недостающие кластеры в top-N — диагностический факт.
    """
    _columns(top_nodes, TOP_COLUMNS, "top_nodes")
    _key(top_nodes, "gid", "top_nodes")
    _key(top_nodes, "rank", "top_nodes")
    expected = run(priority, nodes_roles, top_n=len(top_nodes))
    for column in TOP_COLUMNS:
        _require(top_nodes[column].reset_index(drop=True).equals(expected[column]),
                 f"top_nodes.{column}: нарушена схема, сортировка или совместимость")

    _key(nodes, "gid", "nodes")
    _columns(nodes, ["is_seed"], "nodes")
    _require(nodes.is_seed.isin([True, False]).all(), "nodes.is_seed: нужен bool")
    _key(node_clusters, "gid", "node_clusters")
    _columns(node_clusters, ["cluster_id"], "node_clusters")
    _require(is_integer_dtype(node_clusters.cluster_id.dtype),
             "node_clusters.cluster_id: нужен целый тип")
    _key(clusters, "cluster_id", "clusters")
    _columns(clusters, ["n_nodes", "n_seed", "sum_kzt_internal"], "clusters")
    _columns(nodes_roles, ["cluster_id", "priority_score", "in_kzt", "out_kzt"],
             "nodes_roles")
    _same_ids(nodes.gid, priority.gid, "nodes/priority")
    _same_ids(nodes.gid, node_clusters.gid, "nodes/node_clusters")
    _same_ids(node_clusters.cluster_id, clusters.cluster_id, "node_clusters/clusters")
    cid = node_clusters.set_index("gid").cluster_id
    _require(bool((nodes_roles.cluster_id.to_numpy()
                   == nodes_roles.gid.map(cid).to_numpy()).all()),
             "nodes_roles.cluster_id: расходится с node_clusters")

    _columns(edges, ["src", "dst", "sum_kzt"], "edges")
    for column in ("src", "dst"):
        _require(is_integer_dtype(edges[column].dtype), f"edges.{column}: нужен целый тип")
        _require(edges[column].isin(nodes.gid).all(), f"edges.{column}: неизвестный gid")
    _require(not edges.duplicated(["src", "dst"]).any(), "edges: повторяющиеся пары")
    _numbers(edges.sum_kzt, "edges.sum_kzt")
    for column, endpoint in (("in_kzt", "dst"), ("out_kzt", "src")):
        _numbers(nodes_roles[column], f"nodes_roles.{column}")
        sums = edges.groupby(endpoint).sum_kzt.sum()
        _close(nodes_roles[column], nodes_roles.gid.map(sums).fillna(0),
               f"nodes_roles.{column}")

    summary = clusters.set_index("cluster_id").sort_index()
    sizes = node_clusters.groupby("cluster_id").size().reindex(summary.index)
    seeds = nodes.assign(cluster_id=nodes.gid.map(cid)).groupby("cluster_id").is_seed.sum()
    for column, counts in (("n_nodes", sizes), ("n_seed", seeds.reindex(summary.index))):
        _numbers(summary[column], f"clusters.{column}")
        _require(bool((summary[column] == counts).all()), f"clusters.{column}: неверные количества")
    source, target = edges.src.map(cid), edges.dst.map(cid)
    internal = source == target
    checks = [("sum_kzt_internal", internal, source),
              ("sum_kzt_in_external", ~internal, target),
              ("sum_kzt_out_external", ~internal, source)]
    for column, mask, groups in checks:
        if column not in summary:
            continue
        _numbers(summary[column], f"clusters.{column}")
        sums = edges.loc[mask, "sum_kzt"].groupby(groups[mask]).sum()
        _close(summary[column], sums.reindex(summary.index, fill_value=0), f"clusters.{column}")

    covered = set(top_nodes.gid.map(cid))
    return {
        "n_nodes": len(nodes), "n_top": len(top_nodes),
        "n_clusters": len(clusters), "n_clusters_in_top": len(covered),
        "cluster_coverage": len(covered) / len(clusters),
        "missing_cluster_ids": sorted(set(clusters.cluster_id) - covered),
        "sum_kzt_total": float(edges.sum_kzt.sum()),
        "sum_kzt_internal": float(edges.loc[internal, "sum_kzt"].sum()),
        "sum_kzt_between_clusters": float(edges.loc[~internal, "sum_kzt"].sum()),
    }
