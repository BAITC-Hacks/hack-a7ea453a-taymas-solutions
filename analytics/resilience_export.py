"""PAN-55: предвычисление эксперимента устойчивости (PAN-44) для интерфейса.

Читает готовые выгрузки пайплайна (nodes_roles.csv, edge_table.csv), вызывает
analytics.resilience.run без изменений и пишет out/resilience.json — один раз.
Интерфейс только читает этот файл: смена N не запускает эксперимент заново.

    python -m analytics.resilience_export --out out

Не часть обязательного pipeline: если файла нет, основной экран работает, а
раздел устойчивости сообщает, что бонусный расчёт не выполнен.

Правила сериализации для браузера:
  * gid — строки: 18-значные идентификаторы не помещаются в безопасное целое JS;
  * NaN (неприменимая метрика) → null: интерфейс показывает «не определено», а не 0;
  * json.dumps(allow_nan=False) — невалидный для браузера JSON не может быть записан.
"""

import argparse
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from analytics.resilience import COMPARISON_METRICS, run

SCHEMA = "resilience/v1"
OUTPUT = "resilience.json"
INPUTS = ("nodes_roles.csv", "edge_table.csv")
DETERMINISTIC = ("priority", "degree")
CONTROLS = ("random", "matched_random")
CAVEATS = [
    "Удаление статическое: узлы убираются из наблюдаемого графа, перенаправление потоков не моделируется.",
    "Оборот удалённых рёбер — исторические переводы за июль, а не предотвращённый будущий ущерб.",
    "5–95-й перцентили случайных сценариев описывают их разброс, это не доверительный интервал среднего.",
    "Degree — чисто структурный контроль; AML-приоритет не оптимизирован под разрушение сети и может уступать ему.",
    "Результат — гипотеза о структурной роли узлов в наблюдаемом графе, не вывод о виновности.",
]


def _clean(value):
    """numpy/pandas → JSON-тип; NaN и ±inf → None."""
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _metrics(row: pd.Series) -> dict:
    return {k: _clean(v) for k, v in row.items() if k not in ("strategy", "trial", "n_removed")}


def build_payload(result: dict, sources: dict, timing: dict) -> dict:
    """Результат analytics.resilience.run → структура для интерфейса (чистая функция)."""
    meta = result["metadata"]
    steps = [int(n) for n in meta["steps"]]
    sc, sm, cmp_ = result["scenarios"], result["summary"], result["comparisons"]
    baseline = _metrics(result["baseline"].iloc[0])

    deterministic = {}
    for strategy in DETERMINISTIC:
        rows = sc[sc.strategy == strategy]
        deterministic[strategy] = {str(int(r.n_removed)): _metrics(r) for _, r in rows.iterrows()}

    controls = {}
    for strategy in CONTROLS:
        by_n = {}
        for n, group in sm[sm.strategy == strategy].groupby("n_removed", sort=True):
            by_n[str(int(n))] = {r.metric: {"mean": _clean(r["mean"]), "q05": _clean(r.q05), "q95": _clean(r.q95),
                                            "n_trials": int(r.n_trials), "n_valid": int(r.n_valid)}
                                 for _, r in group.iterrows()}
        controls[strategy] = by_n

    comparisons = {}
    for n, group in cmp_.groupby("n_removed", sort=True):
        comparisons[str(int(n))] = {
            ref: {r.metric: {"priority": _clean(r.priority), "reference_mean": _clean(r.reference_mean),
                             "priority_minus_reference": _clean(r.priority_minus_reference),
                             "reference_fraction_ge_priority": _clean(r.reference_fraction_ge_priority),
                             "n_valid_reference": int(r.n_valid_reference)}
                  for _, r in sub.iterrows()}
            for ref, sub in group.groupby("reference", sort=True)}

    removals = {}
    rm = result["removals"]
    for strategy in DETERMINISTIC:
        rows = rm[rm.strategy == strategy].sort_values("rank")
        removals[strategy] = [str(int(g)) for g in rows.gid]

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": sources,
        "parameters": {"steps": steps, "random_runs": int(meta["random_runs"]), "seed": int(meta["seed"]),
                       "networkx_version": meta["networkx_version"], "ranking": meta["ranking"],
                       "matched_on": list(meta["matched_on"]), "adaptive_ranking": bool(meta["adaptive_ranking"]),
                       "headline_metrics": list(COMPARISON_METRICS)},
        "timing_sec": timing,
        "baseline": baseline,
        "deterministic": deterministic,
        "controls": controls,
        "comparisons": comparisons,
        "removals": removals,
        "interpretation": meta["interpretation"],
        "caveats": CAVEATS,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute(out_dir, random_runs: int = 100, seed: int = 42, steps=None) -> dict:
    """Прочитать выгрузки пайплайна и вернуть payload (без записи на диск)."""
    out_dir = Path(out_dir)
    missing = [f for f in INPUTS if not (out_dir / f).exists()]
    if missing:
        raise FileNotFoundError(f"в {out_dir} нет {', '.join(missing)}: сначала "
                                f"python -m money_graph --data data --out {out_dir}")
    t0 = time.perf_counter()
    nodes = pd.read_csv(out_dir / "nodes_roles.csv", dtype={"gid": "int64"})
    edges = pd.read_csv(out_dir / "edge_table.csv", dtype={"src": "int64", "dst": "int64"})
    nodes["is_seed"] = nodes.is_seed.astype(str).str.lower().map({"true": True, "false": False})
    if nodes.is_seed.isna().any():
        raise ValueError("nodes_roles.csv: is_seed должен быть true/false")
    # priority берётся из официальной выгрузки — порядок удаления совпадает с top_nodes.csv
    t1 = time.perf_counter()
    result = run(edges[["src", "dst", "sum_kzt"]], nodes[["gid", "depth", "is_seed"]],
                 nodes[["gid", "priority_score"]], steps=steps, random_runs=random_runs, seed=seed)
    t2 = time.perf_counter()
    sources = {f: {"sha256": _sha256(out_dir / f)} for f in INPUTS}
    sources["graph"] = {"n_nodes": int(len(nodes)), "n_edges": int(len(edges)),
                        "total_kzt": round(float(edges.sum_kzt.sum()), 2)}
    timing = {"read_inputs": round(t1 - t0, 3), "experiment": round(t2 - t1, 3)}
    payload = build_payload(result, sources, timing)
    payload["timing_sec"]["total"] = round(time.perf_counter() - t0, 3)
    return payload


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=1)


def write(payload: dict, out_dir) -> Path:
    """Атомарная запись: интерфейс никогда не прочитает наполовину записанный файл."""
    path = Path(out_dir) / OUTPUT
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(dumps(payload), encoding="utf-8")
    os.replace(tmp, path)
    return path


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Предвычислить устойчивость сети (PAN-44) для интерфейса")
    ap.add_argument("--out", default="out", help="папка с выгрузками пайплайна; туда же пишется resilience.json")
    ap.add_argument("--random-runs", type=int, default=100, help="прогонов каждого случайного контроля")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)
    try:
        payload = compute(a.out, random_runs=a.random_runs, seed=a.seed)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        sys.exit(2)
    path = write(payload, a.out)
    t = payload["timing_sec"]
    print(f"{path}: N={payload['parameters']['steps']}, {payload['parameters']['random_runs']} прогонов "
          f"каждого контроля, эксперимент {t['experiment']:.1f} с, всего {t['total']:.1f} с")


if __name__ == "__main__":
    main()
