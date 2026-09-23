"""PAN-32: контракт входных данных, таблица рёбер и обработка пустых потоков.

Основная часть тестов идёт на синтетическом мини-графе и не требует архива данных.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from money_graph.features import build_features
from money_graph.graph import build_graph, edge_table
from money_graph.io import InputSchemaError, load, validate_inputs

DATA = Path(__file__).resolve().parents[1] / "data"

# мини-обход: seed 1 → 2 → 3 → 4 → 5 (4-е колено), 2 → 1 — обратное ребро, 6 — seed без рёбер
A, B, Cc, D, E, F = 1, 2, 3, 4, 5, 6


@pytest.fixture
def raw():
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
    edges["depth"] = edges.src.map(nodes.set_index("gid").depth) + 1
    tx["date"] = pd.to_datetime(tx.date).dt.date      # как в parquet: object с datetime.date
    edges["depth"] = edges.depth.astype("int8")
    return edges, nodes, tx


def _fails(edges, nodes, tx, expected: str):
    with pytest.raises(InputSchemaError, match=expected):
        validate_inputs(edges, nodes, tx)


# ---------------------------------------------------------------- контракт

def test_valid_input_passes_and_types_are_canonical(raw):
    edges, nodes, tx, rep = validate_inputs(*raw)
    assert edges.depth.dtype == "int64" and nodes.depth.dtype == "int64"
    assert pd.api.types.is_datetime64_any_dtype(tx.date)
    assert rep.stats["nodes"] == 6 and rep.stats["seeds"] == 2
    joined = " ".join(rep.warnings)
    assert "1 строк полностью повторяют" in joined          # A→B дважды по 10 000 в один день
    assert "1 узлов без рёбер (из них seed 1)" in joined


def test_input_frames_are_not_mutated(raw):
    edges, nodes, tx = raw
    before = tx.date.dtype
    validate_inputs(edges, nodes, tx)
    assert tx.date.dtype == before


@pytest.mark.parametrize("table, mutate, expected", [
    ("edges", lambda d: d.drop(columns="n_tx"), r"нет обязательных колонок \['n_tx'\]"),
    ("nodes", lambda d: d.assign(depth=d.depth.astype(float).where(d.gid != A)), "nodes.depth: 1 пустых"),
    ("nodes", lambda d: d.assign(is_seed=d.is_seed.astype(int)), "ожидался bool"),
    ("nodes", lambda d: pd.concat([d, d.head(1)]), "повторяющихся gid"),
    ("edges", lambda d: pd.concat([d, d.head(1)]), "повторяющихся пар"),
    ("edges", lambda d: d.assign(dst=d.dst.replace(Cc, 99)), "отсутствуют в nodes"),
    ("edges", lambda d: d.assign(sum_kzt=d.sum_kzt.where(d.src != Cc, -1.0)), "неположительных сумм"),
    ("nodes", lambda d: d.assign(depth=d.depth.replace(4, 7)), r"depth: 1 значений вне 0\.\.4"),
    ("nodes", lambda d: d.assign(is_seed=d.depth == 1), "is_seed не совпадает"),
    ("edges", lambda d: d.assign(sum_kzt=d.sum_kzt + 1.0), "расхождение сумм"),
    ("edges", lambda d: d.assign(n_tx=d.n_tx + 1), r"n_tx в \d+ парах"),
    ("transactions", lambda d: d.assign(date="не дата"), "не читаются как дата"),
    ("transactions", lambda d: d[d.src != D], "пар только в edges 1"),
])
def test_contract_violations_are_reported(raw, table, mutate, expected):
    frames = dict(zip(("edges", "nodes", "transactions"), raw))
    frames[table] = mutate(frames[table])
    _fails(frames["edges"], frames["nodes"], frames["transactions"], expected)


def test_all_errors_are_reported_at_once(raw):
    edges, nodes, tx = raw
    with pytest.raises(InputSchemaError) as exc:
        validate_inputs(edges.assign(sum_kzt=edges.sum_kzt + 1), pd.concat([nodes, nodes.head(1)]), tx)
    assert "повторяющихся gid" in str(exc.value) and "расхождение сумм" in str(exc.value)


def test_missing_files_give_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="edges.parquet, nodes.parquet, transactions.parquet"):
        load(tmp_path)


# ---------------------------------------------------------------- таблица рёбер

def test_edge_table(raw):
    edges, nodes, tx, _ = validate_inputs(*raw)
    et = edge_table(edges, nodes, tx).set_index(["src", "dst"])
    assert len(et) == len(edges) and et.sum_kzt.sum() == edges.sum_kzt.sum()
    ab = et.loc[(A, B)]
    assert ab.n_tx == 2 and ab.n_repeat_tx == 1 and ab.avg_tx_kzt == 10_000 and ab.active_days == 1
    assert ab.is_reciprocal and not ab.is_back_edge
    ba = et.loc[(B, A)]
    assert ba.is_back_edge and ba.is_reciprocal                  # деньги вернулись к seed
    assert et.loc[(B, Cc)].share_of_src_out == pytest.approx(0.75)  # 15 000 из 20 000
    assert not et.loc[(Cc, D)].is_reciprocal


# ---------------------------------------------------------------- пустые потоки и граница

@pytest.fixture
def features(raw):
    edges, nodes, tx, _ = validate_inputs(*raw)
    G = build_graph(edge_table(edges, nodes, tx), nodes)
    return build_features(G, nodes, tx).set_index("gid")


def test_empty_flows_are_zero_not_nan(features):
    f = features.loc[F]                                          # seed без рёбер
    assert (f.in_deg, f.out_deg, f.in_kzt, f.out_kzt, f.in_tx, f.out_tx) == (0, 0, 0.0, 0.0, 0, 0)
    assert f.avg_in_tx_kzt == 0.0 and f.avg_out_tx_kzt == 0.0
    assert f.boundary == "isolated"


def test_undefined_ratios_are_nan(features):
    e = features.loc[E]                                          # нет исходящих
    assert np.isnan(e.top_receiver_share) and np.isnan(e.fast_out_share)
    a = features.loc[A]                                          # seed: вход занижен
    assert np.isnan(a.pass_through_reliable) and a.boundary == "in_hidden"


def test_boundary_depth4(features):
    e = features.loc[E]
    assert e.boundary_depth4 and not e.out_observable and e.boundary == "out_hidden"
    d = features.loc[D]                                          # depth 3: отток наблюдаем
    assert not d.boundary_depth4 and d.out_observable
    assert features.boundary_depth4.sum() == 1


# ---------------------------------------------------------------- реальные данные

@pytest.mark.skipif(not (DATA / "edges.parquet").exists(),
                    reason="нет data/edges.parquet — распакуйте архив данных в ./data")
def test_real_dataset_contract():
    edges, nodes, tx, rep = validate_inputs(*load(DATA))
    assert rep.stats["nodes"] == 2248 and rep.stats["edges"] == 3119 and rep.stats["transactions"] == 4840
    assert rep.stats["seeds"] == 81 and round(rep.stats["turnover_kzt"]) == 365_890_012
    et = edge_table(edges, nodes, tx)
    assert len(et) == 3119 and et.n_repeat_tx.sum() == 97 and not et.isna().any().any()
