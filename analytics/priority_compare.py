"""PAN-57: объяснение разницы приоритетов и диагностика устойчивости пары.

Функции работают в памяти. compare принимает полную nodes_roles-таблицу;
pair_stability — все узлы с колонками analytics.priority.run. Сравнение
рёбер и общих контрагентов остаётся в GraphTools.compare_nodes.
Контракт, округление и ограничения: docs/priority_compare.md.
"""

import hashlib
import json
from numbers import Integral, Real

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_numeric_dtype

from agent_tools.errors import InvalidArgumentError
from agent_tools.store import parse_gid, py
from agent_tools.tools import BRIEF_FIELDS, _pick, node_warnings
from analytics.priority import (SCORE_DECIMALS, WEIGHTS, _score_order, _sensitivity_inputs,
                                _weight_scenarios, _weighted_scores)

CONTRIB = [f"contrib_{k}" for k in WEIGHTS]
PRIORITY_FIELDS = ["gid", "priority_score", *CONTRIB, "boundary_factor"]
CONTEXT_FIELDS = ["role", "role_score", "depth", "is_seed", "in_kzt", "out_kzt",
                  "n_payers", "n_receivers", "n_seed_payers", "n_seed_receivers",
                  "in_deg", "out_deg", "out_observable", "external_inflow_suspected"]
# Каждый из шести вкладов и итоговый score округлены независимо до 4 знаков.
SCORE_TOLERANCE = (len(WEIGHTS) + 1) * 0.5 * 10 ** -SCORE_DECIMALS
LIMITATIONS = [
    "Устойчивость к весам ≠ вероятность вины, ≠ точность модели и не подтверждает роль.",
    "Граф ограничен глубиной 4, переводами ≥5 000 KZT внутри банка и датами с точностью до дня; "
    "входящие извне выборки не видны.",
    "Ранги и top-K относятся ко всем переданным строкам: передавайте полную таблицу узлов.",
]
LABELS = {"collect": "сбор", "fanout": "рассылка", "flow": "транзит/оседание",
          "seed": "связь с seed", "bridge": "мост", "volume": "объём"}


def _gid(value) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError("gid должен быть целым числом или строкой цифр")
    try:
        return parse_gid(value)
    except InvalidArgumentError as exc:
        raise ValueError(str(exc)) from exc


def _prepare(pr, gid_a, gid_b, *, context=False):
    a, b = _gid(gid_a), _gid(gid_b)
    if a == b:
        raise ValueError("gid_a и gid_b должны различаться: выбран один и тот же gid")
    if not isinstance(pr, pd.DataFrame):
        raise ValueError("pr: нужен pandas.DataFrame со всеми узлами")
    if not pr.columns.is_unique:
        raise ValueError("pr: повторяющиеся колонки")
    required = PRIORITY_FIELDS + (CONTEXT_FIELDS if context else [])
    missing = [c for c in required if c not in pr]
    if missing:
        raise ValueError(f"pr: недостающие диагностические колонки: {', '.join(missing)}")
    if pr[required].isna().any().any():
        raise ValueError("pr: диагностические колонки содержат пустые значения")
    frame = pr.reset_index(drop=True).copy()
    frame["gid"] = frame.gid.map(_gid)
    if not frame.gid.is_unique:
        raise ValueError("pr: gid должны быть уникальны")
    unknown = sorted({a, b} - set(frame.gid))
    if unknown:
        raise ValueError(f"pr: неизвестный gid: {', '.join(map(str, unknown))}")
    for c in PRIORITY_FIELDS[1:]:
        values = frame[c]
        if (not is_numeric_dtype(values) or is_bool_dtype(values)
                or not np.isfinite(values).all() or (values < 0).any()):
            raise ValueError(f"pr.{c}: нужны конечные неотрицательные числа")
    if not frame.priority_score.between(0, 1).all():
        raise ValueError("pr.priority_score: нужен score в [0, 1]")
    if not ((frame.boundary_factor > 0) & (frame.boundary_factor <= 1)).all():
        raise ValueError("pr.boundary_factor: нужен множитель в (0, 1]")
    for k, w in WEIGHTS.items():
        if (frame[f"contrib_{k}"] > w + 0.5 * 10 ** -SCORE_DECIMALS).any():
            raise ValueError(f"pr.contrib_{k}: вклад превышает исходный вес {w}")
    rebuilt = _weighted_scores(*_sensitivity_inputs(frame))
    if (np.abs(rebuilt - frame.priority_score.to_numpy()) > SCORE_TOLERANCE + 1e-12).any():
        raise ValueError(f"pr: score не воспроизводится из вкладов с допуском {SCORE_TOLERANCE:g}")
    if context:
        for c in ("is_seed", "out_observable", "external_inflow_suspected"):
            if not is_bool_dtype(frame[c]):
                raise ValueError(f"pr.{c}: нужен bool")
        if not frame.role.map(lambda v: isinstance(v, str) and bool(v.strip())).all():
            raise ValueError("pr.role: нужна непустая строка")
        for c in set(CONTEXT_FIELDS) - {"role", "is_seed", "out_observable", "external_inflow_suspected"}:
            if (not is_numeric_dtype(frame[c]) or is_bool_dtype(frame[c])
                    or not np.isfinite(frame[c]).all() or (frame[c] < 0).any()):
                raise ValueError(f"pr.{c}: нужны конечные неотрицательные числа")
    return frame.sort_values("gid").reset_index(drop=True), a, b


def _provenance(frame, columns):
    # Хеш только использованных данных: независим от индекса, строк и порядка колонок.
    rows = [[str(v) if c == "gid" else py(v) for c, v in zip(columns, row)]
            for row in frame[columns].itertuples(index=False, name=None)]
    payload = json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return {"data_version": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "n_nodes": len(frame), "source": {"table": "pr", "columns": columns},
            "weights": dict(WEIGHTS), "score_tolerance": SCORE_TOLERANCE}


def _ranks(frame, score):
    ranks = np.empty(len(frame), dtype=int)
    ranks[_score_order(frame, score)] = np.arange(1, len(frame) + 1)
    return ranks


def compare(pr: pd.DataFrame, gid_a, gid_b) -> dict:
    """Разница A − B, официальный ранг (с 1), вклады, причины и ограничения.

    pr — полная nodes_roles.csv / GraphStore.nodes; priority.run не содержит
    ролей и границ наблюдения и сам по себе для compare недостаточен.
    gid принимается целым или строкой, в результате всегда строка.
    Невалидные аргументы/диагностика вызывают ValueError.
    """
    frame, a, b = _prepare(pr, gid_a, gid_b, context=True)
    ranks = _ranks(frame, frame.priority_score.to_numpy())
    fields = list(dict.fromkeys([*BRIEF_FIELDS, *CONTEXT_FIELDS, *CONTRIB, "boundary_factor"]))
    fields = [c for c in fields if c in frame]
    nodes = []
    for gid in (a, b):
        i = frame.index[frame.gid == gid][0]
        row = frame.loc[i]
        node = _pick(row, fields)
        node["gid"] = str(gid)
        node["rank"] = int(ranks[i])
        raw = sum(float(row[c]) for c in CONTRIB)
        node["score_from_contrib"] = raw * float(row.boundary_factor)
        node["boundary_adjustment"] = raw * (float(row.boundary_factor) - 1)
        node["rounding_residual"] = float(row.priority_score) - node["score_from_contrib"]
        node["warnings"] = [{**w, "gid": str(gid)} for w in node_warnings(row)]
        for warning in node["warnings"]:
            if warning["code"] == "external_inflow":
                warning["message"] = (f"вход неполный: out_kzt={row.out_kzt:.2f}, in_kzt={row.in_kzt:.2f}; "
                                      + warning["message"])
        nodes.append(node)
    na, nb = nodes
    delta_fields = ["priority_score", "rank", *CONTRIB, "boundary_factor",
                    "boundary_adjustment", "rounding_residual"]
    delta = {c: na[c] - nb[c] for c in delta_fields}
    reasons = []
    labels = {**{f"contrib_{k}": v for k, v in LABELS.items()},
              "boundary_adjustment": "поправка границы наблюдения"}
    for c in sorted(labels, key=lambda c: (-abs(delta[c]), c)):
        if abs(delta[c]) < 1e-12:
            continue
        reasons.append({"component": c, "a": na[c], "b": nb[c], "delta": delta[c],
                        "message": f"{labels[c]}: A {na[c]:.6f}, B {nb[c]:.6f}; A − B {delta[c]:+.6f}"})
    return {"a": na, "b": nb, "delta": delta, "main_reasons": reasons[:3],
            "a_above_b": na["rank"] < nb["rank"],
            "order_reason": "priority_score" if delta["priority_score"] else "gid_ascending",
            **_provenance(frame, fields), "limitations": list(LIMITATIONS)}


def _integer(value, name, minimum):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name}: нужно целое ≥ {minimum}")


def pair_stability(pr: pd.DataFrame, gid_a, gid_b, top_k: int = 20, spread: float = 0.5,
                   n_runs: int = 1000, seed: int = 0) -> dict:
    """Ранги и частоты в n_runs сценариях с весами × U(1−spread, 1+spread).

    Формула и RNG общие с weight_sensitivity; веса не нормируются. При точных
    исходных весах берётся опубликованный priority_score (округлённые вклады
    не позволяют восстановить его побитово). Базовый сценарий возвращается
    отдельно и не добавляется к n_runs. Полная семантика — в документации.
    """
    _integer(top_k, "top_k", 1)
    _integer(n_runs, "n_runs", 1)
    _integer(seed, "seed", 0)
    if (isinstance(spread, (bool, np.bool_)) or not isinstance(spread, Real)
            or not np.isfinite(spread) or not 0 <= spread <= 1):
        raise ValueError("spread: нужно конечное число в [0, 1]")
    frame, a, b = _prepare(pr, gid_a, gid_b)
    positions = [int(frame.index[frame.gid == g][0]) for g in (a, b)]
    comp, factor, w0 = _sensitivity_inputs(frame)
    official = frame.priority_score.to_numpy()
    base_ranks = _ranks(frame, official)[positions]
    pair_ranks = np.empty((n_runs, 2), dtype=int)
    for i, weights in enumerate(_weight_scenarios(w0, spread, n_runs, seed)):
        # Сохраняем официальный tie-break даже если округление вкладов меняет порядок.
        score = official if np.array_equal(weights, w0) else _weighted_scores(comp, factor, weights)
        pair_ranks[i] = _ranks(frame, score)[positions]

    def summary(j, gid):
        ranks = pair_ranks[:, j]
        hits = int((ranks <= top_k).sum())
        return {"gid": str(gid), "baseline_rank": int(base_ranks[j]),
                "rank_min": int(ranks.min()), "rank_max": int(ranks.max()),
                "top_k_count": hits, "top_k_frequency": hits / n_runs}

    above = int((pair_ranks[:, 0] < pair_ranks[:, 1]).sum())
    return {"a": summary(0, a), "b": summary(1, b),
            "a_above_b_count": above, "a_above_b_frequency": above / n_runs,
            "baseline": {"a_above_b": bool(base_ranks[0] < base_ranks[1]),
                         "score_source": "priority_score", "tie_break": "gid_ascending"},
            "parameters": {"top_k": int(top_k), "effective_top_k": min(int(top_k), len(frame)),
                           "spread": float(spread), "n_runs": int(n_runs), "seed": int(seed),
                           "numpy_version": np.__version__,
                           "sampling": "numpy.default_rng / weights * U(1-spread, 1+spread)",
                           "scenario_score": "unrounded weighted contributions; official score at base weights"},
            **_provenance(frame, PRIORITY_FIELDS), "limitations": list(LIMITATIONS)}
