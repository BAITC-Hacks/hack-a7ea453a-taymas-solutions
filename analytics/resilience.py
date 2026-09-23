"""PAN-44: статическое удаление узлов и сравнение устойчивости графа.

run(edges, nodes, priority) возвращает таблицы, ничего не пишет на диск.
Не зависит от ролей или основного pipeline. Формулы и ограничения — docs/resilience.md.
"""

from collections import Counter
from math import fsum
from numbers import Integral

import networkx as nx
import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_integer_dtype, is_numeric_dtype

DEFAULT_STEPS = (1, 5, 10, 20, 50)
COMPARISON_METRICS = ("weak_pair_loss", "seed_reach_loss", "removed_kzt_share")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _columns(frame, columns, name):
    _require(frame.columns.is_unique, f"{name}: повторяющиеся колонки")
    _require(set(columns) <= set(frame.columns), f"{name}: нужны колонки {columns}")
    _require(not frame[columns].isna().any().any(), f"{name}: пустые значения")


def _integer(value, name, minimum):
    _require(isinstance(value, Integral) and not isinstance(value, (bool, np.bool_))
             and value >= minimum, f"{name}: нужно целое >= {minimum}")


def _validate(edges, nodes, priority, steps, random_runs, seed):
    _columns(nodes, ["gid", "depth", "is_seed"], "nodes")
    _columns(edges, ["src", "dst", "sum_kzt"], "edges")
    _columns(priority, ["gid", "priority_score"], "priority")
    for name, frame, column in (("nodes", nodes, "gid"), ("priority", priority, "gid"),
                                 ("edges", edges, "src"), ("edges", edges, "dst")):
        _require(is_integer_dtype(frame[column]), f"{name}.{column}: нужен целый тип")
    _require(len(nodes) > 0 and nodes.gid.is_unique, "nodes: нужен непустой уникальный gid")
    _require(priority.gid.is_unique and set(priority.gid) == set(nodes.gid),
             "priority: нужно ровно одно значение для каждого gid из nodes")
    _require(is_integer_dtype(nodes.depth) and nodes.depth.between(0, 4).all(),
             "nodes.depth: нужны целые 0..4")
    _require(is_bool_dtype(nodes.is_seed), "nodes.is_seed: нужен bool")
    _require(edges.src.isin(nodes.gid).all() and edges.dst.isin(nodes.gid).all(),
             "edges: неизвестный gid")
    _require(not edges.duplicated(["src", "dst"]).any(), "edges: повторяющиеся пары")
    for name, values, upper in (("sum_kzt", edges.sum_kzt, None),
                                 ("priority_score", priority.priority_score, 1)):
        _require(is_numeric_dtype(values) and not is_bool_dtype(values), f"{name}: нужен числовой тип")
        _require(np.isfinite(values).all() and (values >= 0).all(), f"{name}: нужны конечные числа >=0")
        if upper is not None:
            _require((values <= upper).all(), f"{name}: значения выше {upper}")
    _require((edges.sum_kzt > 0).all(), "edges.sum_kzt: веса должны быть положительными")
    _integer(random_runs, "random_runs", 1)
    _integer(seed, "seed", 0)
    if steps is None:
        steps = [n for n in DEFAULT_STEPS if n <= len(nodes)]
    else:
        steps = list(steps)
        _require(bool(steps), "steps: нужен хотя бы один размер удаления")
    for n in steps:
        _integer(n, "steps", 0)
        _require(n <= len(nodes), "steps: удаление больше числа узлов")
    return sorted(set([0, *steps]))


def _reachable(graph, seeds):
    """Объединение достижимых узлов: один обход, только исходящие рёбра."""
    seen = set(seeds)
    stack = list(sorted(seeds))
    while stack:
        for other in graph.successors(stack.pop()):
            if other not in seen:
                seen.add(other)
                stack.append(other)
    return seen - set(seeds)


def _pairs(sizes):
    return sum(n * (n - 1) // 2 for n in sizes)


def _measure(graph, removed, seeds, component, originally_isolated, total_kzt):
    remaining = set(graph) - removed
    after = graph.subgraph(remaining)
    weak_sizes = [len(c) for c in nx.weakly_connected_components(after)]
    # Только пары СОХРАНИВШИХСЯ узлов в одной исходной компоненте.
    possible_pairs = _pairs(Counter(component[g] for g in remaining).values())
    connected_pairs = _pairs(weak_sizes)
    surviving_seeds = seeds & remaining
    before_reach = _reachable(graph, surviving_seeds) & remaining
    after_reach = _reachable(after, surviving_seeds)
    isolated = set(nx.isolates(after))
    remaining_kzt = fsum(d["sum_kzt"] for _, _, d in after.edges(data=True))
    return {
        "n_remaining": len(after), "n_edges_remaining": after.number_of_edges(),
        "weak_components": len(weak_sizes), "largest_weak_size": max(weak_sizes, default=0),
        "largest_weak_share_original": max(weak_sizes, default=0) / len(graph),
        "largest_strong_size": max((len(c) for c in nx.strongly_connected_components(after)), default=0),
        "isolates": len(isolated), "new_isolates": len(isolated - originally_isolated),
        "survivor_pairs_before": possible_pairs, "survivor_pairs_after": connected_pairs,
        "weak_pair_loss": 1 - connected_pairs / possible_pairs if possible_pairs else np.nan,
        "n_seed_remaining": len(surviving_seeds),
        "seed_reachable_before": len(before_reach), "seed_reachable_after": len(after_reach),
        "seed_reach_loss": 1 - len(after_reach) / len(before_reach) if before_reach else np.nan,
        "remaining_kzt": remaining_kzt, "removed_kzt": total_kzt - remaining_kzt,
        "removed_kzt_share": 1 - remaining_kzt / total_kzt if total_kzt else np.nan,
    }


def _orders(graph, nodes, priority_order, random_runs, seed, limit):
    gids = sorted(graph)
    # Статический degree, без повторного ранжирования после удаления.
    degree_order = sorted(gids, key=lambda g: (-graph.degree(g), g))
    yield "priority", 0, priority_order[:limit]
    yield "degree", 0, degree_order[:limit]
    strata = {r.gid: (bool(r.is_seed), int(r.depth)) for r in nodes.itertuples(index=False)}
    pools = {}
    for gid in gids:
        pools.setdefault(strata[gid], []).append(gid)
    # Независимые RNG для двух контролей; строки входных таблиц не влияют на выборку.
    uniform_seed, matched_seed = np.random.SeedSequence(seed).spawn(2)
    uniform_rng = np.random.default_rng(uniform_seed)
    matched_rng = np.random.default_rng(matched_seed)
    for trial in range(random_runs):
        yield "random", trial, uniform_rng.permutation(gids)[:limit].tolist()
        shuffled = {s: iter(matched_rng.permutation(pools[s]).tolist()) for s in sorted(pools)}
        # Каждый префикс имеет тот же состав seed/depth, что и priority.
        matched = [next(shuffled[strata[g]]) for g in priority_order[:limit]]
        yield "matched_random", trial, matched


def _summarize(scenarios):
    rows = []
    metrics = [c for c in scenarios if c not in ("strategy", "trial", "n_removed")]
    for (strategy, n), group in scenarios.groupby(["strategy", "n_removed"], sort=True):
        for metric in metrics:
            values = group[metric].dropna()
            rows.append({"strategy": strategy, "n_removed": n, "metric": metric,
                         "n_trials": len(group), "n_valid": len(values),
                         "mean": values.mean(), "q05": values.quantile(0.05),
                         "q95": values.quantile(0.95)})
    return pd.DataFrame(rows)


def _compare(scenarios):
    rows = []
    for n, group in scenarios.groupby("n_removed", sort=True):
        observed = group[group.strategy == "priority"].iloc[0]
        for strategy in ("degree", "random", "matched_random"):
            for metric in COMPARISON_METRICS:
                values = group.loc[group.strategy == strategy, metric].dropna()
                valid = len(values) > 0 and pd.notna(observed[metric])
                rows.append({"n_removed": n, "reference": strategy, "metric": metric,
                             "priority": observed[metric], "reference_mean": values.mean(),
                             "priority_minus_reference": observed[metric] - values.mean(),
                             "reference_fraction_ge_priority": float((values >= observed[metric]).mean()) if valid else np.nan,
                             "n_valid_reference": len(values)})
    return pd.DataFrame(rows)


def run(edges: pd.DataFrame, nodes: pd.DataFrame, priority: pd.DataFrame,
        steps=None, random_runs: int = 100, seed: int = 42) -> dict:
    """Вернуть baseline/scenarios/summary/comparisons/removals/metadata.

    steps по умолчанию: 0,1,5,10,20,50 (не больше размера графа).
    Таблица priority должна покрывать ВСЕ узлы; достаточно gid/priority_score.
    Порядок совпадает с top_nodes: score ↓, числовой gid ↑.
    Неопределённые доли (нулевой знаменатель) возвращаются как NaN.
    """
    steps = _validate(edges, nodes, priority, steps, random_runs, seed)
    graph = nx.DiGraph()
    graph.add_nodes_from(sorted(nodes.gid.tolist()))
    for r in edges.sort_values(["src", "dst"]).itertuples(index=False):
        graph.add_edge(r.src, r.dst, sum_kzt=float(r.sum_kzt))
    total = fsum(d["sum_kzt"] for _, _, d in graph.edges(data=True))
    _require(np.isfinite(total), "sum_kzt: переполнение общего оборота")
    component = {g: i for i, members in enumerate(nx.weakly_connected_components(graph)) for g in members}
    seeds = set(nodes.loc[nodes.is_seed, "gid"])
    isolated = set(nx.isolates(graph))
    baseline = _measure(graph, set(), seeds, component, isolated, total)
    priority_order = priority.sort_values(["priority_score", "gid"], ascending=[False, True]).gid.tolist()
    scenarios, removals = [], []
    for strategy, trial, order in _orders(graph, nodes, priority_order, random_runs, seed, max(steps)):
        removals.extend({"strategy": strategy, "trial": trial, "rank": i, "gid": g}
                        for i, g in enumerate(order, start=1))
        for n in steps:
            metrics = baseline if n == 0 else _measure(graph, set(order[:n]), seeds, component, isolated, total)
            scenarios.append({"strategy": strategy, "trial": trial, "n_removed": n, **metrics})
    scenarios = pd.DataFrame(scenarios)
    return {
        "baseline": pd.DataFrame([baseline]), "scenarios": scenarios,
        "summary": _summarize(scenarios), "comparisons": _compare(scenarios),
        "removals": pd.DataFrame(removals, columns=["strategy", "trial", "rank", "gid"]),
        "metadata": {"steps": steps, "random_runs": int(random_runs), "seed": int(seed),
                     "networkx_version": nx.__version__, "ranking": "priority_score desc, gid asc",
                     "matched_on": ["is_seed", "depth"], "adaptive_ranking": False,
                     "interpretation": "Гипотеза о структурной роли в наблюдаемом графе; не вывод о виновности или будущем движении денег."},
    }
