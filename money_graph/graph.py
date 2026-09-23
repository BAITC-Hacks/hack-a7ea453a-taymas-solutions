"""Сборка направленного взвешенного графа (перенесено из starter.py)."""

import networkx as nx
import pandas as pd


def build_graph(edges: pd.DataFrame, nodes: pd.DataFrame) -> nx.DiGraph:
    """Направленный граф. sum_kzt — вес ребра, n_tx — количество переводов.

    В отличие от starter, узлы-сироты (seed без рёбер) тоже добавляются в граф,
    чтобы все метрики считались по полному списку из nodes.parquet.
    """
    G = nx.DiGraph()
    G.add_nodes_from(sorted(nodes.gid))
    for r in edges.sort_values(["src", "dst"]).itertuples(index=False):
        G.add_edge(r.src, r.dst, sum_kzt=float(r.sum_kzt), n_tx=int(r.n_tx), depth=int(r.depth))
    return G
