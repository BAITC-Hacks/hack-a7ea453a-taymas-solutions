"""Таблица направленных рёбер и сборка графа.

edge_table — одна строка на пару плательщик → получатель с признаками, которых
нет в edges.parquet: даты, средний и максимальный перевод, повторы, место ребра
в обходе и доля ребра в потоке плательщика / получателя. Её использует граф
и интерфейс (связи узла в карточке).
"""

import networkx as nx
import numpy as np
import pandas as pd


def edge_table(edges: pd.DataFrame, nodes: pd.DataFrame, tx: pd.DataFrame) -> pd.DataFrame:
    """Направленная таблица рёбер. Суммы — KZT за период выгрузки, даты — дни.

    * first_date / last_date / active_days — когда пара была активна;
    * avg_tx_kzt / max_tx_kzt — средний и крупнейший перевод пары;
    * n_repeat_tx — переводов, в точности повторяющих другой перевод пары
      (тот же день и сумма), — кандидаты на дробление;
    * src_depth / dst_depth, is_back_edge — ребро ведёт к узлу, найденному
      раньше или на том же колене (деньги идут «назад» по обходу);
    * is_reciprocal — есть обратное ребро dst → src;
    * share_of_src_out / share_of_dst_in — доля ребра в исходящем потоке
      плательщика и во входящем потоке получателя (0–1).
    """
    t = tx.sort_values(["src", "dst", "date", "sum_kzt"])
    per_pair = t.groupby(["src", "dst"]).agg(
        first_date=("date", "min"), last_date=("date", "max"), active_days=("date", "nunique"),
        max_tx_kzt=("sum_kzt", "max"))
    repeats = t.duplicated(["src", "dst", "date", "sum_kzt"]).groupby([t.src, t.dst]).sum().rename("n_repeat_tx")

    e = edges.sort_values(["src", "dst"]).reset_index(drop=True)
    e = e.merge(per_pair, on=["src", "dst"], how="left").merge(repeats, on=["src", "dst"], how="left")
    e["n_repeat_tx"] = e.n_repeat_tx.fillna(0).astype("int64")
    e["avg_tx_kzt"] = e.sum_kzt / e.n_tx

    depth = nodes.set_index("gid").depth
    seed = nodes.set_index("gid").is_seed
    e["src_depth"] = e.src.map(depth).astype("int64")
    e["dst_depth"] = e.dst.map(depth).astype("int64")
    e["src_is_seed"] = e.src.map(seed).astype(bool)
    e["dst_is_seed"] = e.dst.map(seed).astype(bool)
    e["is_back_edge"] = e.dst_depth <= e.src_depth

    pairs = set(zip(e.src, e.dst))
    e["is_reciprocal"] = [(d, s) in pairs for s, d in zip(e.src, e.dst)]
    e["share_of_src_out"] = e.sum_kzt / e.groupby("src").sum_kzt.transform("sum")
    e["share_of_dst_in"] = e.sum_kzt / e.groupby("dst").sum_kzt.transform("sum")

    cols = ["src", "dst", "sum_kzt", "n_tx", "avg_tx_kzt", "max_tx_kzt", "n_repeat_tx",
            "first_date", "last_date", "active_days", "depth", "src_depth", "dst_depth",
            "src_is_seed", "dst_is_seed", "is_back_edge", "is_reciprocal",
            "share_of_src_out", "share_of_dst_in"]
    return e[cols]


def build_graph(edge_tbl: pd.DataFrame, nodes: pd.DataFrame) -> nx.DiGraph:
    """Направленный граф. sum_kzt — вес ребра, n_tx — количество переводов.

    В отличие от starter, узлы-сироты (seed без рёбер) тоже добавляются в граф,
    чтобы все метрики считались по полному списку из nodes.parquet.
    """
    G = nx.DiGraph()
    G.add_nodes_from(sorted(nodes.gid))
    for r in edge_tbl.sort_values(["src", "dst"]).itertuples(index=False):
        G.add_edge(r.src, r.dst, sum_kzt=float(r.sum_kzt), n_tx=int(r.n_tx), depth=int(r.depth))
    return G


def edge_table_for_csv(edge_tbl: pd.DataFrame) -> pd.DataFrame:
    out = edge_tbl.copy()
    for col in ("first_date", "last_date"):
        out[col] = out[col].dt.strftime("%Y-%m-%d")
    for col in ("avg_tx_kzt", "share_of_src_out", "share_of_dst_in"):
        out[col] = np.round(out[col], 6)
    return out
