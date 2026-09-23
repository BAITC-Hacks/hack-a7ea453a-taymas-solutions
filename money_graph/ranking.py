"""Кластеры (из analytics.clustering, PAN-35) и приоритет.

priority_score и top_nodes — ВРЕМЕННАЯ версия до сабтикета «Приоритет и топ-лист».
Интерфейс сохранить: функции добавляют в df колонку priority_score и возвращают top_nodes.
"""

import numpy as np
import pandas as pd

from analytics.clustering import run as run_clustering

ROLE_WEIGHT = {"coordinator": 1.0, "consolidator": 0.8, "distributor": 0.75,
               "transit": 0.6, "terminal": 0.5, "peripheral": 0.1}


def assign_clusters(df: pd.DataFrame, edges: pd.DataFrame, nodes: pd.DataFrame):
    """cluster_id из Louvain-кластеризации PAN-35 (docs/clustering.md) → (df, clusters)."""
    node_clusters, clusters = run_clustering(edges, nodes)
    df = df.drop(columns=["cluster_id"], errors="ignore").merge(
        node_clusters[["gid", "cluster_id"]], on="gid", how="left", validate="one_to_one")
    return df, clusters


def assign_priority(df: pd.DataFrame) -> pd.DataFrame:
    """Сумма весов = 1, каждое слагаемое в [0, 1] → priority_score в [0, 1]."""
    pct = lambda s: s.rank(method="average", pct=True)
    df["priority_score"] = (
        0.35 * df.role.map(ROLE_WEIGHT) * df.role_score
        + 0.20 * pct(df.pagerank)
        + 0.15 * pct(df.betweenness)
        + 0.15 * pct(df.in_kzt + df.out_kzt)
        + 0.10 * np.clip(df.n_seed_payers / 3, 0, 1)
        + 0.05 * (df.n_cycles > 0)
    ).clip(0, 1).round(4)
    return df


def top_nodes_table(df: pd.DataFrame, n: int = 30) -> pd.DataFrame:
    top = df.sort_values(["priority_score", "gid"], ascending=[False, True]).head(n).reset_index(drop=True)
    return pd.DataFrame({"rank": np.arange(1, len(top) + 1), "gid": top.gid, "role": top.role,
                         "priority_score": top.priority_score, "why": top.evidence})
