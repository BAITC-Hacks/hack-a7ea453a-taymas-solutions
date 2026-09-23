"""Схема выгрузок, её механическая проверка и сериализация в CSV."""

from pathlib import Path

import pandas as pd

from analytics.priority import WEIGHTS as PRIORITY_WEIGHTS

from . import config as C
from .roles import ROLES

OUTPUT_FILES = ("nodes_roles.csv", "clusters.csv", "top_nodes.csv", "edge_table.csv")

# обязательная схема из ТЗ: колонка → ожидаемый вид
REQUIRED = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]
REQUIRED_KIND = {"gid": "int", "role": "str", "role_score": "float", "cluster_id": "int",
                 "priority_score": "float", "evidence": "str"}
CLUSTERS_REQUIRED = ["cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"]
TOP_REQUIRED = ["rank", "gid", "role", "priority_score", "why"]
TOP_MIN_ROWS = 20

# дополнительные колонки (ТЗ разрешает лишние): разбор priority_score из analytics.priority —
# объяснение с числами и вклад каждой компоненты — и признаки, на которые опирается роль
PRIORITY_EXTRA = ["priority_why", *(f"contrib_{k}" for k in PRIORITY_WEIGHTS), "boundary_factor"]
EXTRA = [
    "role_rule", *PRIORITY_EXTRA,
    "depth", "is_seed", "boundary", "boundary_depth4", "truncated_by_depth", "out_observable",
    "in_underestimated", "external_inflow_suspected",
    "in_deg", "out_deg", "n_payers", "n_receivers", "n_seed_payers", "n_seed_receivers",
    "in_kzt", "out_kzt", "in_tx", "out_tx", "avg_in_tx_kzt", "avg_out_tx_kzt",
    "top_payer_share", "top_receiver_share", "pass_through", "pass_through_reliable",
    "pagerank", "betweenness", "hub_score", "authority_score", "downstream_reach", "n_seed_upstream",
    "wcc_id", "wcc_size",
    "fast_out_share", "median_lag_days", "sync_payers_max", "max_tx_per_day", "active_days",
    "n_cycles", "min_cycle_len", "has_long_cycle",
]
FLOAT_ROUND = 6


class OutputSchemaError(ValueError):
    """Выгрузки нарушают схему ТЗ."""


def nodes_roles_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df[REQUIRED + EXTRA].sort_values("gid").reset_index(drop=True)
    # целый тип только при полной колонке: пропуски должна поймать validate_outputs
    # (понятная ошибка, код выхода 3), а не astype с traceback
    for col in ("gid", "cluster_id"):
        if out[col].notna().all():
            out[col] = out[col].astype("int64")
    floats = out.select_dtypes("float").columns
    out[floats] = out[floats].round(FLOAT_ROUND)
    return out


def _kind_ok(s: pd.Series, kind: str) -> bool:
    if kind == "int":
        return pd.api.types.is_integer_dtype(s)
    if kind == "float":
        return pd.api.types.is_float_dtype(s)
    return s.map(lambda v: isinstance(v, str)).all()


def validate_outputs(nr: pd.DataFrame, nodes: pd.DataFrame, clusters: pd.DataFrame,
                     top: pd.DataFrame) -> list[str]:
    """Механическая проверка выгрузок по ТЗ. Возвращает список пройденных проверок,
    при нарушениях бросает OutputSchemaError со всеми проблемами сразу."""
    problems, passed = [], []

    def check(name: str, ok: bool, problem: str):
        (passed if ok else problems).append(name if ok else f"{name}: {problem}")

    # --- nodes_roles.csv
    missing = [c for c in REQUIRED if c not in nr.columns]
    check("nodes_roles: обязательные колонки", not missing, f"нет {missing}")
    if missing:
        raise OutputSchemaError("Выгрузки не прошли проверку:\n  " + "\n  ".join(problems))
    check("nodes_roles: обязательные колонки идут первыми", list(nr.columns[:len(REQUIRED)]) == REQUIRED,
          f"порядок {list(nr.columns[:len(REQUIRED)])}")
    check(f"nodes_roles: строка на каждый узел ({len(nodes)})", len(nr) == len(nodes),
          f"{len(nr)} строк вместо {len(nodes)}")
    check("nodes_roles: gid уникальны", not nr.gid.duplicated().any(), f"{int(nr.gid.duplicated().sum())} дубликатов")
    lost, extra = set(nodes.gid) - set(nr.gid), set(nr.gid) - set(nodes.gid)
    check("nodes_roles: набор gid совпадает с nodes.parquet", not lost and not extra,
          f"нет {len(lost)} узлов, лишних {len(extra)}")
    nulls = {c: int(n) for c, n in nr[REQUIRED].isna().sum().items() if n}
    blank = int(((nr.role.astype(str).str.strip() == "") | (nr.evidence.astype(str).str.strip() == "")).sum())
    check("nodes_roles: обязательные поля заполнены", not nulls and not blank,
          f"пустые значения {nulls}, пустых строк {blank}")
    bad_kind = [c for c, k in REQUIRED_KIND.items() if not _kind_ok(nr[c].dropna(), k)]
    check("nodes_roles: типы колонок", not bad_kind, f"неверный тип у {bad_kind}")
    check("nodes_roles: role из словаря ТЗ", nr.role.isin(ROLES).all(), f"лишние роли {set(nr.role) - set(ROLES)}")
    for col in ("role_score", "priority_score"):
        out_of = int((~nr[col].between(0, 1)).sum())
        check(f"nodes_roles: {col} в [0, 1]", out_of == 0, f"{out_of} значений вне диапазона")
    long_ev = int((nr.evidence.astype(str).str.len() > C.EVIDENCE_MAX_LEN).sum())
    check(f"nodes_roles: evidence ≤ {C.EVIDENCE_MAX_LEN} символов", long_ev == 0, f"{long_ev} длиннее")
    no_digit = int((~nr.evidence.astype(str).str.contains(r"\d")).sum())
    check("nodes_roles: evidence содержит числа", no_digit == 0, f"{no_digit} без чисел")
    if "depth" in nr.columns:
        d4_term = int(((nr.depth == C.MAX_DEPTH) & (nr.role == "terminal")).sum())
        check(f"nodes_roles: depth={C.MAX_DEPTH} не получает terminal", d4_term == 0, f"{d4_term} узлов")

    # --- clusters.csv
    c_missing = [c for c in CLUSTERS_REQUIRED if c not in clusters.columns]
    check("clusters: обязательные колонки", not c_missing, f"нет {c_missing}")
    if not c_missing:
        check("clusters: cluster_id уникальны", not clusters.cluster_id.duplicated().any(), "есть дубликаты")
        check("clusters: каждый cluster_id узлов описан", set(nr.cluster_id) <= set(clusters.cluster_id),
              f"не описаны {sorted(set(nr.cluster_id) - set(clusters.cluster_id))[:5]}")
        check("clusters: n_nodes в сумме = числу узлов", int(clusters.n_nodes.sum()) == len(nr),
              f"{int(clusters.n_nodes.sum())} ≠ {len(nr)}")
        check("clusters: n_seed в сумме = числу seed", int(clusters.n_seed.sum()) == int(nodes.is_seed.sum()),
              f"{int(clusters.n_seed.sum())} ≠ {int(nodes.is_seed.sum())}")
        check("clusters: hypothesis заполнена", clusters.hypothesis.astype(str).str.strip().ne("").all()
              and clusters.hypothesis.notna().all(), "есть пустые")

    # --- top_nodes.csv
    t_missing = [c for c in TOP_REQUIRED if c not in top.columns]
    check("top_nodes: обязательные колонки", not t_missing, f"нет {t_missing}")
    if not t_missing:
        need = min(TOP_MIN_ROWS, len(nr))
        check(f"top_nodes: не меньше {need} строк", len(top) >= need, f"{len(top)} строк")
        check("top_nodes: rank = 1..N", top["rank"].tolist() == list(range(1, len(top) + 1)), "нарушена нумерация")
        check("top_nodes: отсортирован по priority_score", top.priority_score.is_monotonic_decreasing,
              "порядок нарушен")
        cols = ["gid", "role", "priority_score"] + (["priority_why"] if "priority_why" in nr.columns else [])
        joined = top.merge(nr[cols], on="gid", how="left", suffixes=("", "_nr"))
        check("top_nodes: gid, role и priority_score согласованы с nodes_roles",
              joined.role_nr.notna().all() and (joined.role == joined.role_nr).all()
              and (joined.priority_score - joined.priority_score_nr).abs().max() < 1e-9,
              "расхождение с nodes_roles")
        if "priority_why" in joined.columns:
            differs = int((joined.why != joined.priority_why).sum())
            check("top_nodes: why совпадает с priority_why в nodes_roles", differs == 0, f"{differs} расхождений")
        check("top_nodes: why заполнен", top.why.notna().all() and top.why.astype(str).str.strip().ne("").all(),
              "есть пустые")

    if problems:
        raise OutputSchemaError("Выгрузки не прошли проверку:\n  " + "\n  ".join(problems))
    return passed


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """CSV в UTF-8 с переводом строки \\n на любой ОС — чтобы sha256 совпадал между машинами."""
    return df.to_csv(index=False, lineterminator="\n").encode("utf-8")


def write_outputs(files: dict[str, bytes], out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
