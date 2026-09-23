"""Синтетический мини-граф для тестов, которым не нужен архив данных.

Мини-обход: seed 1 → 2 → 3 → 4 → 5 (4-е колено), 2 → 1 — обратное ребро,
6 — seed без рёбер. A → B — два одинаковых перевода в один день.
"""

from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parents[1] / "data"
HAS_DATA = (DATA / "edges.parquet").exists()

A, B, Cc, D, E, F = 1, 2, 3, 4, 5, 6


def mini_frames():
    """(edges, nodes, tx) в том же виде, что parquet организаторов."""
    nodes = pd.DataFrame({"gid": [A, B, Cc, D, E, F], "depth": [0, 1, 2, 3, 4, 0],
                          "is_seed": [True, False, False, False, False, True]})
    tx = pd.DataFrame({
        "src": [A, A, B, B, Cc, D],
        "dst": [B, B, Cc, A, D, E],
        "date": ["2026-07-01", "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-05", "2026-07-06"],
        "sum_kzt": [10_000.0, 10_000.0, 15_000.0, 5_000.0, 15_000.0, 14_000.0],
    })
    edges = (tx.groupby(["src", "dst"]).agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
             .reset_index())
    edges["depth"] = (edges.src.map(nodes.set_index("gid").depth) + 1).astype("int8")
    tx["date"] = pd.to_datetime(tx.date).dt.date      # как в parquet: object с datetime.date
    return edges, nodes, tx


def write_mini_parquet(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    edges, nodes, tx = mini_frames()
    edges.to_parquet(data_dir / "edges.parquet", index=False)
    nodes.to_parquet(data_dir / "nodes.parquet", index=False)
    tx.to_parquet(data_dir / "transactions.parquet", index=False)
    return data_dir
