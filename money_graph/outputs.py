"""Запись выгрузок и механическая проверка схемы из ТЗ."""

from pathlib import Path

import pandas as pd

from . import config as C
from .roles import ROLES

REQUIRED = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]

# дополнительные колонки: признаки, на которые опирается роль (ТЗ разрешает лишние колонки)
EXTRA = [
    "role_rule", "depth", "is_seed", "boundary", "boundary_depth4", "truncated_by_depth", "out_observable",
    "in_underestimated", "external_inflow_suspected",
    "in_deg", "out_deg", "n_payers", "n_receivers", "n_seed_payers", "n_seed_receivers",
    "in_kzt", "out_kzt", "in_tx", "out_tx", "avg_in_tx_kzt", "avg_out_tx_kzt",
    "top_payer_share", "top_receiver_share", "pass_through", "pass_through_reliable",
    "pagerank", "betweenness", "hub_score", "authority_score", "downstream_reach", "n_seed_upstream",
    "wcc_id", "wcc_size",
    "fast_out_share", "median_lag_days", "sync_payers_max", "max_tx_per_day", "active_days",
    "n_cycles", "min_cycle_len",
]
FLOAT_ROUND = 6


def nodes_roles_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df[REQUIRED + EXTRA].sort_values("gid").reset_index(drop=True)
    out["gid"] = out.gid.astype("int64")
    out["cluster_id"] = out.cluster_id.astype(int)
    floats = out.select_dtypes("float").columns
    out[floats] = out[floats].round(FLOAT_ROUND)
    return out


def validate(nodes_roles: pd.DataFrame, n_expected: int, clusters: pd.DataFrame, top: pd.DataFrame):
    nr = nodes_roles
    problems = []
    if len(nr) != n_expected:
        problems.append(f"nodes_roles: {len(nr)} строк вместо {n_expected}")
    if nr.gid.duplicated().any():
        problems.append("nodes_roles: дубликаты gid")
    blank = (nr.role.astype(str).str.strip() == "") | (nr.evidence.astype(str).str.strip() == "")
    if nr[REQUIRED].isna().any().any() or blank.any():
        problems.append("nodes_roles: пустые обязательные поля")
    if not nr.role.isin(ROLES).all():
        problems.append(f"nodes_roles: роли вне словаря {set(nr.role) - set(ROLES)}")
    for col in ("role_score", "priority_score"):
        if not nr[col].between(0, 1).all():
            problems.append(f"nodes_roles: {col} вне [0, 1]")
    if (nr.evidence.str.len() > C.EVIDENCE_MAX_LEN).any():
        problems.append(f"nodes_roles: evidence длиннее {C.EVIDENCE_MAX_LEN}")
    if not nr.evidence.str.contains(r"\d").all():
        problems.append("nodes_roles: evidence без чисел")
    if ((nr.depth == C.MAX_DEPTH) & (nr.role == "terminal")).any():
        problems.append("nodes_roles: узел 4-го колена получил terminal")
    if not set(nr.cluster_id) <= set(clusters.cluster_id):
        problems.append("clusters: не все cluster_id описаны")
    if len(top) < 20:
        problems.append("top_nodes: меньше 20 строк")
    if problems:
        raise AssertionError("Выгрузки не прошли проверку:\n  " + "\n  ".join(problems))


def write_outputs(nodes_roles: pd.DataFrame, clusters: pd.DataFrame, top: pd.DataFrame,
                  edges: pd.DataFrame, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    edges.to_csv(out_dir / "edge_table.csv", index=False, encoding="utf-8")
    nodes_roles.to_csv(out_dir / "nodes_roles.csv", index=False, encoding="utf-8")
    clusters.to_csv(out_dir / "clusters.csv", index=False, encoding="utf-8")
    top.to_csv(out_dir / "top_nodes.csv", index=False, encoding="utf-8")
