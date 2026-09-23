"""Загрузка parquet и проверка входного контракта данных.

Проверки делятся на два уровня:
  * ошибки — вход нарушает контракт, строить роли на нём нельзя: прогон останавливается
    с полным списком проблем (InputSchemaError);
  * предупреждения — особенности выгрузки, которые пайплайн учитывает, но о которых
    аналитик должен знать (печатаются в отчёт проверки).

После проверки типы приводятся к каноническим: gid/src/dst/n_tx/depth — int64,
sum_kzt — float64 (KZT), is_seed — bool, date — datetime64 (день).
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C

FILES = ("edges", "nodes", "transactions")

# колонка → ожидаемый вид: int / float / bool / date
SCHEMA = {
    "edges": {"src": "int", "dst": "int", "sum_kzt": "float", "n_tx": "int", "depth": "int"},
    "nodes": {"gid": "int", "depth": "int", "is_seed": "bool"},
    "transactions": {"src": "int", "dst": "int", "date": "date", "sum_kzt": "float"},
}
SUM_TOLERANCE_KZT = 0.01   # допуск сверки сумм edges ↔ transactions (ошибка округления float)


class InputSchemaError(ValueError):
    """Входные parquet нарушают контракт данных."""


@dataclass
class InputReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def load(data_dir: Path):
    """Читает три parquet как есть. Отсутствующие файлы — понятная ошибка, а не traceback."""
    data_dir = Path(data_dir)
    missing = [f"{name}.parquet" for name in FILES if not (data_dir / f"{name}.parquet").exists()]
    if missing:
        raise FileNotFoundError(
            f"в {data_dir.resolve()} нет файлов: {', '.join(missing)}. "
            f"Распакуйте архив данных организаторов в эту папку или укажите --data")
    return tuple(pd.read_parquet(data_dir / f"{name}.parquet") for name in FILES)


# ---------------------------------------------------------------- типы

def _coerce(df: pd.DataFrame, table: str, rep: InputReport) -> pd.DataFrame:
    """Проверяет наличие колонок, пропуски и приводимость типов; возвращает копию с каноническими типами."""
    schema = SCHEMA[table]
    missing = [c for c in schema if c not in df.columns]
    if missing:
        rep.errors.append(f"{table}: нет обязательных колонок {missing}")
        return df
    out = df.copy()
    for col, kind in schema.items():
        s = out[col]
        n_null = int(s.isna().sum())
        if n_null:
            rep.errors.append(f"{table}.{col}: {n_null} пустых значений (NaN/NA)")
            continue
        if kind == "int":
            if not pd.api.types.is_integer_dtype(s):
                num = pd.to_numeric(s, errors="coerce")
                if num.isna().any() or not np.isfinite(num).all():
                    rep.errors.append(f"{table}.{col}: ожидались конечные целые числа, NaN/Infinity недопустимы")
                    continue
                if (num % 1 != 0).any():
                    rep.errors.append(f"{table}.{col}: ожидались целые, тип {s.dtype}")
                    continue
                s = num
            out[col] = s.astype("int64")
        elif kind == "float":
            num = pd.to_numeric(s, errors="coerce").astype("float64")
            if not np.isfinite(num).all():
                rep.errors.append(f"{table}.{col}: ожидались конечные числа, NaN/Infinity недопустимы")
                continue
            out[col] = num
        elif kind == "bool":
            if not pd.api.types.is_bool_dtype(s):
                rep.errors.append(f"{table}.{col}: ожидался bool, тип {s.dtype}")
                continue
        elif kind == "date":
            dt = pd.to_datetime(s, errors="coerce", format="ISO8601")   # YYYY-MM-DD или date
            if dt.isna().any():
                rep.errors.append(f"{table}.{col}: {int(dt.isna().sum())} значений не читаются как дата")
                continue
            out[col] = dt.dt.normalize()
    return out


# ---------------------------------------------------------------- контракт

def _check_contract(edges, nodes, tx, rep: InputReport):
    # --- уникальность
    dup_gid = int(nodes.gid.duplicated().sum())
    if dup_gid:
        rep.errors.append(f"nodes: {dup_gid} повторяющихся gid")
    dup_pair = int(edges.duplicated(["src", "dst"]).sum())
    if dup_pair:
        rep.errors.append(f"edges: {dup_pair} повторяющихся пар src→dst (ожидается агрегат пары)")

    # --- покрытие gid: каждое ребро и транзакция ссылаются на известный узел
    known = set(nodes.gid)
    for table, df in (("edges", edges), ("transactions", tx)):
        unknown = (set(df.src) | set(df.dst)) - known
        if unknown:
            rep.errors.append(f"{table}: {len(unknown)} gid отсутствуют в nodes, например {min(unknown)}")

    # --- домены значений
    for table, df in (("edges", edges), ("transactions", tx)):
        bad = int((df.sum_kzt <= 0).sum())
        if bad:
            rep.errors.append(f"{table}.sum_kzt: {bad} неположительных сумм")
    if (edges.n_tx < 1).any():
        rep.errors.append(f"edges.n_tx: {int((edges.n_tx < 1).sum())} значений < 1")
    for table, df, lo in (("nodes", nodes, 0), ("edges", edges, 1)):
        out_of_range = int((~df.depth.between(lo, C.MAX_DEPTH)).sum())
        if out_of_range:
            rep.errors.append(f"{table}.depth: {out_of_range} значений вне {lo}..{C.MAX_DEPTH}")
    seed_mismatch = int(((nodes.depth == 0) != nodes.is_seed).sum())
    if seed_mismatch:
        rep.errors.append(f"nodes: у {seed_mismatch} узлов is_seed не совпадает с depth=0")

    # Finite rows can still overflow when summed, even if every pair reconciles.
    with np.errstate(over="ignore", invalid="ignore"):
        totals = {table: float(df.sum_kzt.sum()) for table, df in (("edges", edges), ("transactions", tx))}
    for table, total in totals.items():
        if not np.isfinite(total):
            rep.errors.append(f"{table}.sum_kzt: переполнение общей суммы (NaN/Infinity)")

    # --- сверка агрегатов: edges — это сумма transactions по паре
    agg = tx.groupby(["src", "dst"]).agg(tx_sum=("sum_kzt", "sum"), tx_cnt=("sum_kzt", "size")).reset_index()
    bad_agg = int((~np.isfinite(agg.tx_sum)).sum())
    if bad_agg:
        rep.errors.append(f"transactions.sum_kzt: переполнение агрегата в {bad_agg} парах (NaN/Infinity)")
    m = edges.merge(agg, on=["src", "dst"], how="outer", indicator=True)
    only_e, only_t = int((m._merge == "left_only").sum()), int((m._merge == "right_only").sum())
    if only_e or only_t:
        rep.errors.append(f"edges↔transactions: пар только в edges {only_e}, только в transactions {only_t}")
    both = m[m._merge == "both"]
    with np.errstate(over="ignore", invalid="ignore"):
        delta = both.sum_kzt - both.tx_sum
    nonfinite_diff = ~np.isfinite(delta)
    if nonfinite_diff.any():
        rep.errors.append(f"edges↔transactions.sum_kzt: неконечная разность в {int(nonfinite_diff.sum())} парах")
    sum_diff = int((nonfinite_diff | (delta.abs() > SUM_TOLERANCE_KZT)).sum())
    cnt_diff = int((both.n_tx != both.tx_cnt).sum())
    if sum_diff or cnt_diff:
        rep.errors.append(f"edges↔transactions: расхождение сумм в {sum_diff} парах, n_tx в {cnt_diff} парах")

    # --- предупреждения: особенности выгрузки, которые пайплайн учитывает.
    # Считаются только на корректном входе: им нужны уникальные gid и полное покрытие.
    if rep.errors:
        return
    self_loops = int((edges.src == edges.dst).sum())
    if self_loops:
        rep.warnings.append(f"edges: {self_loops} переводов самому себе")
    below = int((tx.sum_kzt < C.MIN_TX_KZT).sum())
    if below:
        rep.warnings.append(f"transactions: {below} переводов ниже порога выгрузки {C.MIN_TX_KZT:,} KZT")
    depth = nodes.set_index("gid").depth
    not_crawl = int((edges.depth != edges.src.map(depth) + 1).sum())
    if not_crawl:
        rep.warnings.append(f"edges: у {not_crawl} рёбер depth ≠ depth(src)+1 — выгрузка собрана не обходом от seed")
    dup_tx = int(tx.duplicated().sum())
    if dup_tx:
        rep.warnings.append(f"transactions: {dup_tx} строк полностью повторяют другую (пара, день, сумма) — "
                            f"сохранены: n_tx их учитывает, возможный признак дробления")
    orphans = known - set(edges.src) - set(edges.dst)
    if orphans:
        n_seed = int(nodes[nodes.gid.isin(orphans)].is_seed.sum())
        rep.warnings.append(f"nodes: {len(orphans)} узлов без рёбер (из них seed {n_seed}) — войдут в выгрузку")
    truncated = int((nodes.depth == C.MAX_DEPTH).sum())
    rep.warnings.append(f"nodes: {truncated} узлов на {C.MAX_DEPTH}-м колене — их исходящие не выгружались")

    rep.stats.update({
        "nodes": len(nodes), "seeds": int(nodes.is_seed.sum()), "edges": len(edges), "transactions": len(tx),
        "turnover_kzt": totals["edges"],
        "period": (tx.date.min().date(), tx.date.max().date()) if len(tx) else (None, None),
    })


def validate_inputs(edges: pd.DataFrame, nodes: pd.DataFrame, tx: pd.DataFrame):
    """Проверяет контракт и возвращает (edges, nodes, tx, report) с каноническими типами.

    Бросает InputSchemaError со списком всех ошибок сразу, чтобы не чинить вход по одной.
    """
    rep = InputReport()
    edges = _coerce(edges, "edges", rep)
    nodes = _coerce(nodes, "nodes", rep)
    tx = _coerce(tx, "transactions", rep)
    if not rep.errors:
        _check_contract(edges, nodes, tx, rep)
    if rep.errors:
        raise InputSchemaError("Входные данные не прошли проверку:\n  " + "\n  ".join(rep.errors))
    return edges, nodes, tx, rep


def print_report(rep: InputReport):
    s = rep.stats
    print("=" * 64)
    print("ПРОВЕРКА ВХОДНЫХ ДАННЫХ: OK")
    print("=" * 64)
    print(f"  узлов                 : {s['nodes']:>6}  (seed: {s['seeds']})")
    print(f"  рёбер                 : {s['edges']:>6}")
    print(f"  транзакций            : {s['transactions']:>6}")
    print(f"  оборот, KZT           : {s['turnover_kzt']:>14,.0f}")
    print(f"  период                : {s['period'][0]} — {s['period'][1]}")
    print("  edges == transactions : пары, суммы и n_tx сходятся")
    for w in rep.warnings:
        print(f"  ! {w}")
    print("=" * 64, "\n")
