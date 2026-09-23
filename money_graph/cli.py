"""Точка входа: сырые parquet → признаки → роли → три CSV.

Запуск:
    python -m money_graph --data data --out out
"""

import argparse
import sys
import time
from pathlib import Path

from .features import build_features
from .graph import build_graph, edge_table, edge_table_for_csv
from .io import InputSchemaError, load, print_report, validate_inputs
from .outputs import nodes_roles_table, validate, write_outputs
from .ranking import assign_clusters, assign_priority, top_nodes_table
from .roles import ROLES, assign_roles


def summary(df):
    print("РОЛИ")
    print("-" * 64)
    counts = df.role.value_counts()
    for role in ROLES:
        rules = df[df.role == role].role_rule.value_counts()
        detail = ", ".join(f"{r}={n}" for r, n in rules.items())
        print(f"  {role:<13}: {counts.get(role, 0):>5}   ({detail})")
    d4 = df[df.depth == 4]
    print(f"\n  depth=4: {len(d4)} узлов, terminal среди них: {(d4.role == 'terminal').sum()}")
    print("-" * 64)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):   # консоль Windows по умолчанию cp1251
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Граф денег: роли, кластеры, приоритеты")
    ap.add_argument("--data", default="data", help="папка с edges/nodes/transactions.parquet")
    ap.add_argument("--out", default="out", help="куда писать выгрузки")
    a = ap.parse_args(argv)

    t0 = time.perf_counter()
    try:
        edges, nodes, tx, report = validate_inputs(*load(Path(a.data)))
    except (FileNotFoundError, InputSchemaError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        sys.exit(2)
    print_report(report)
    edges_tbl = edge_table(edges, nodes, tx)
    G = build_graph(edges_tbl, nodes)
    df = build_features(G, nodes, tx)
    df = assign_roles(df)
    df, clusters = assign_clusters(df, edges, nodes)
    df = assign_priority(df)

    nodes_roles = nodes_roles_table(df)
    top = top_nodes_table(df)
    validate(nodes_roles, len(nodes), clusters, top)
    write_outputs(nodes_roles, clusters, top, edge_table_for_csv(edges_tbl), Path(a.out))

    summary(df)
    print(f"Выгрузки записаны в {Path(a.out)}/ за {time.perf_counter() - t0:.1f} с; проверка схемы: OK")


if __name__ == "__main__":
    main()
