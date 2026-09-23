"""Кластеры, приоритет и топ-лист — через модули analytics (PAN-35/36/37).

  cluster_id      analytics.clustering  (docs/clustering.md)
  priority_score  analytics.priority    (docs/priority.md)
  top_nodes       analytics.top_nodes   (docs/top_nodes.md)

Интерфейс: функции добавляют колонки в df и возвращают top_nodes. Скор и why
считаются один раз и лежат в df, поэтому nodes_roles.csv и top_nodes.csv
берут их из одного источника и расходиться не могут.
"""

import pandas as pd

from analytics.clustering import run as run_clustering
from analytics.priority import OUTPUT_COLUMNS as PRIORITY_OUTPUT
from analytics.priority import run as run_priority
from analytics.top_nodes import audit
from analytics.top_nodes import run as run_top_nodes

from .outputs import OutputSchemaError

TOP_N = 30
# why из analytics.priority объясняет скор; в nodes_roles он лежит рядом с evidence
# (объяснением роли) под именем priority_why
PRIORITY_COLUMNS = ["priority_why" if c == "why" else c for c in PRIORITY_OUTPUT if c != "gid"]


def assign_clusters(df: pd.DataFrame, edges: pd.DataFrame, nodes: pd.DataFrame):
    """cluster_id из Louvain-кластеризации PAN-35 (docs/clustering.md) → (df, clusters)."""
    node_clusters, clusters = run_clustering(edges, nodes)
    df = df.drop(columns=["cluster_id"], errors="ignore").merge(
        node_clusters[["gid", "cluster_id"]], on="gid", how="left", validate="one_to_one")
    return df, clusters


def assign_priority(df: pd.DataFrame, edges: pd.DataFrame, nodes: pd.DataFrame) -> pd.DataFrame:
    """priority_score, priority_why и вклад компонент из PAN-36; нужен cluster_id в df."""
    pr = run_priority(edges, nodes, df[["gid", "cluster_id"]]).rename(columns={"why": "priority_why"})
    return df.drop(columns=PRIORITY_COLUMNS, errors="ignore").merge(
        pr, on="gid", how="left", validate="one_to_one")


def _priority(nr: pd.DataFrame) -> pd.DataFrame:
    """Вход PAN-37 в том виде, который возвращает analytics.priority.run."""
    return nr[["gid", "priority_score", "priority_why"]].rename(columns={"priority_why": "why"})


def top_nodes_table(df: pd.DataFrame, n: int = TOP_N) -> pd.DataFrame:
    """Топ по priority_score (PAN-37): rank, gid, role, priority_score, why."""
    try:
        return run_top_nodes(_priority(df), df[["gid", "role", "priority_score"]], top_n=min(n, len(df)))
    except ValueError as exc:
        raise OutputSchemaError(f"top_nodes: {exc}") from exc


def audit_top_nodes(nr: pd.DataFrame, top: pd.DataFrame, edges: pd.DataFrame, nodes: pd.DataFrame,
                    clusters: pd.DataFrame) -> str:
    """Аудит PAN-37 по готовым таблицам: топ пересобирается из nodes_roles и сверяется
    построчно, суммы узлов и кластеров — с рёбрами. Возвращает строку для списка проверок."""
    try:
        rep = audit(top, _priority(nr), nr[["gid", "role", "priority_score", "cluster_id", "in_kzt", "out_kzt"]],
                    edges, nodes, nr[["gid", "cluster_id"]], clusters)
    except ValueError as exc:
        raise OutputSchemaError(f"Выгрузки не прошли аудит top_nodes:\n  {exc}") from exc
    return (f"top_nodes: аудит PAN-37 (топ = первые {rep['n_top']} по priority_score, суммы узлов и кластеров "
            f"сходятся с рёбрами; в топе {rep['n_clusters_in_top']} из {rep['n_clusters']} кластеров)")
