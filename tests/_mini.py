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


def frames_from_tx(rows, depth: dict):
    """(edges, nodes, tx) из списка переводов (src, dst, 'YYYY-MM-DD', сумма) и колен узлов.

    seed — узлы с depth=0. Рёбра агрегируются из транзакций, как в выгрузке.
    """
    tx = pd.DataFrame(rows, columns=["src", "dst", "date", "sum_kzt"])
    tx["sum_kzt"] = tx.sum_kzt.astype(float)
    nodes = pd.DataFrame({"gid": list(depth), "depth": list(depth.values())})
    nodes["is_seed"] = nodes.depth == 0
    edges = (tx.groupby(["src", "dst"]).agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
             .reset_index())
    edges["depth"] = (edges.src.map(depth) + 1).astype("int8")
    tx["date"] = pd.to_datetime(tx.date).dt.date
    return edges, nodes, tx


def write_mini_parquet(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    edges, nodes, tx = mini_frames()
    edges.to_parquet(data_dir / "edges.parquet", index=False)
    nodes.to_parquet(data_dir / "nodes.parquet", index=False)
    tx.to_parquet(data_dir / "transactions.parquet", index=False)
    return data_dir


# ---------------------------------------------------------------- граф для инструментов агента
#   S1 ─100к→ C ←50к─ S2 ─10к→ L            C — общий сборщик S1 и S2 напрямую
#   S1 ─30к→ M ─30к→ K ←20к─ N ←20к─ S3     K — общий сборщик S1 и S3 через посредников
#   C ─140к→ D ─100к→ E ─90к→ F (4-е колено) цепочка вниз до границы обхода
#   I — seed без переводов
S1, S2, S3, I = 1, 2, 3, 4
C, M, N, L, D, K, E, F = 11, 12, 13, 14, 21, 22, 31, 41
AGENT_ROWS = [
    (S1, C, "2026-07-01", 100_000), (S2, C, "2026-07-01", 50_000), (S2, L, "2026-07-02", 10_000),
    (S1, M, "2026-07-01", 30_000), (M, K, "2026-07-02", 30_000),
    (S3, N, "2026-07-01", 20_000), (N, K, "2026-07-02", 20_000),
    (C, D, "2026-07-02", 90_000), (C, D, "2026-07-03", 50_000),
    (D, E, "2026-07-04", 100_000), (E, F, "2026-07-05", 90_000),
]
AGENT_DEPTH = {S1: 0, S2: 0, S3: 0, I: 0, C: 1, M: 1, N: 1, L: 1, D: 2, K: 2, E: 3, F: 4}


def build_outputs(root: Path, rows=AGENT_ROWS, depth=AGENT_DEPTH) -> Path:
    """Parquet из переводов → настоящий пайплайн → папка out с выгрузками."""
    from money_graph.cli import main as run_pipeline

    edges, nodes, tx = frames_from_tx(rows, depth)
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    edges.to_parquet(data / "edges.parquet", index=False)
    nodes.to_parquet(data / "nodes.parquet", index=False)
    tx.to_parquet(data / "transactions.parquet", index=False)
    run_pipeline(["--data", str(data), "--out", str(root / "out")])
    return root / "out"
