"""
priority_score: кого из клиентов смотреть первым и почему (PAN-36).

Формула — взвешенная сумма шести компонент, каждая в [0, 1]:

    priority_score = boundary_factor × Σ WEIGHTS[k] · component[k]

  collect  — сбор: сколько разных плательщиков сверх первого
  fanout   — рассылка: сколько разных получателей сверх первого
  flow     — транзит или оседание: ушли ли полученные деньги дальше
             (out ≈ in) или остались у узла, собравшего их от многих
  seed     — прямая связь с seed: от скольких разных seed узел получил деньги
  bridge   — мост: со сколькими другими кластерами узел обменивается деньгами
  volume   — объём: max(вход, выход) в тенге

Счётчики и суммы шкалируются логарифмически к максимуму по графу:
log1p(x) / log1p(max). Разница между 1 и 5 плательщиками важнее, чем между
20 и 24, и одиночный выброс не сжимает остальных в ноль.

boundary_factor = 0.8 для узлов 4-го колена без исходящих: обход на них
остановился, их исходящие не выгружались, поэтому половина поведения неизвестна.

Модуль только считает: run(...) возвращает DataFrame и ничего не пишет на диск.

Запуск отдельно (кластеры пересчитываются, ~2 с):
    python -m analytics.priority --data data --out out
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

WEIGHTS = {
    "collect": 0.25,
    "fanout": 0.15,
    "flow": 0.15,
    "seed": 0.15,
    "bridge": 0.15,
    "volume": 0.15,
}
BOUNDARY_FACTOR = 0.8       # множитель для узлов, где обход остановился (4-е колено без исходящих)
MAX_DEPTH = 4               # глубина обхода из DATA_README: узлы этого колена не раскрывались
MIN_EDGE_KZT = 5_000        # порог выгрузки: переводы меньше в данные не попали
SCORE_DECIMALS = 4          # округление; при равенстве порядок — по gid
WHY_MIN_CONTRIB = 0.02      # компоненты с меньшим вкладом не упоминаются в why
WHY_MAX_PARTS = 3

OUTPUT_COLUMNS = (["gid", "priority_score", "why"]
                  + [f"contrib_{k}" for k in WEIGHTS] + ["boundary_factor"])


# ---------------------------------------------------------------- признаки

def _log_scale(x: pd.Series) -> pd.Series:
    """log1p(x) / log1p(max): 0 → 0, максимум по графу → 1."""
    top = float(x.max())
    if top <= 0:
        return pd.Series(0.0, index=x.index)
    return np.log1p(x.astype(float)) / np.log1p(top)


def _features(edges: pd.DataFrame, nodes: pd.DataFrame,
              node_clusters: pd.DataFrame) -> pd.DataFrame:
    """Сырые признаки узла — всё, из чего собираются компоненты и why."""
    f = nodes.set_index("gid")[["depth", "is_seed"]].sort_index()
    idx = f.index

    by_dst, by_src = edges.groupby("dst"), edges.groupby("src")
    f["in_deg"] = by_dst.src.nunique().reindex(idx, fill_value=0)
    f["out_deg"] = by_src.dst.nunique().reindex(idx, fill_value=0)
    f["in_kzt"] = by_dst.sum_kzt.sum().reindex(idx, fill_value=0.0)
    f["out_kzt"] = by_src.sum_kzt.sum().reindex(idx, fill_value=0.0)
    f["in_tx"] = by_dst.n_tx.sum().reindex(idx, fill_value=0)
    f["out_tx"] = by_src.n_tx.sum().reindex(idx, fill_value=0)

    seeds = set(nodes.gid[nodes.is_seed])
    from_seed = edges[edges.src.isin(seeds)]
    f["seed_payers"] = from_seed.groupby("dst").src.nunique().reindex(idx, fill_value=0)

    # другие кластеры среди контрагентов (и плательщиков, и получателей)
    cid = node_clusters.set_index("gid").cluster_id
    pairs = pd.concat([
        pd.DataFrame({"gid": edges.src, "own": edges.src.map(cid), "other": edges.dst.map(cid)}),
        pd.DataFrame({"gid": edges.dst, "own": edges.dst.map(cid), "other": edges.src.map(cid)}),
    ])
    pairs = pairs[pairs.own != pairs.other]
    f["bridge_clusters"] = pairs.groupby("gid").other.nunique().reindex(idx, fill_value=0)

    # обход остановился на узле: исходящие не выгружались, out_deg == 0 ничего не значит
    f["truncated"] = (f.depth >= MAX_DEPTH) & (f.out_deg == 0)
    return f


def _components(f: pd.DataFrame) -> pd.DataFrame:
    """Шесть компонент в [0, 1]. Смысл и обоснование — docs/priority.md."""
    c = pd.DataFrame(index=f.index)

    # один плательщик есть у 78% узлов просто по устройству обхода — сигналом
    # считается только «сверх первого»; с получателями так же
    c["collect"] = _log_scale((f.in_deg - 1).clip(lower=0))
    c["fanout"] = _log_scale((f.out_deg - 1).clip(lower=0))

    # транзит / оседание оценивается только там, где видны обе стороны:
    # у seed вход занижен выгрузкой, у обрезанных узлов нет данных о выходе
    observed = ~f.is_seed & ~f.truncated & (f.in_kzt > 0)
    in_kzt = f.in_kzt.where(f.in_kzt > 0)
    balance = np.minimum(f.in_kzt, f.out_kzt) / np.maximum(f.in_kzt, f.out_kzt).where(lambda s: s > 0)
    retained = (1 - f.out_kzt / in_kzt).clip(lower=0)
    # оседание засчитывается пропорционально сбору: деньги одного плательщика,
    # оставшиеся у получателя, — обычный платёж, а не консолидация
    consolidation = retained * c["collect"]
    c["flow"] = np.where(observed, np.maximum(balance, consolidation).fillna(0.0), 0.0)
    c["flow_is_transit"] = observed & (balance.fillna(0) >= consolidation.fillna(0))

    c["seed"] = _log_scale(f.seed_payers)
    c["bridge"] = _log_scale(f.bridge_clusters)
    c["volume"] = _log_scale(np.maximum(f.in_kzt, f.out_kzt) / MIN_EDGE_KZT)
    return c


# ---------------------------------------------------------------- why

def _kzt(x: float) -> str:
    return f"{x / 1e6:.1f} млн KZT" if x >= 1e6 else f"{x / 1e3:.0f} тыс. KZT"


def _phrase(k: str, r) -> str:
    if k == "collect":
        return f"сбор: {r.in_deg} плательщ., {r.in_tx} перев."
    if k == "fanout":
        return f"рассылка: {r.out_deg} получат., {r.out_tx} перев."
    if k == "flow":
        if r.flow_is_transit:
            return f"транзит: отправил {r.out_kzt / r.in_kzt:.0%} полученного"
        return f"оседание: оставил {1 - r.out_kzt / r.in_kzt:.0%} из {_kzt(r.in_kzt)}"
    if k == "seed":
        return f"получил от {r.seed_payers} seed"
    if k == "bridge":
        return f"связан с {r.bridge_clusters} др. кластерами"
    return f"оборот {_kzt(max(r.in_kzt, r.out_kzt))}"


def _why(r) -> str:
    if r.in_deg == 0 and r.out_deg == 0:
        return "нет переводов ≥5 000 KZT в выборке — оценить нечего"
    contrib = sorted(((getattr(r, f"contrib_{k}"), k) for k in WEIGHTS), key=lambda t: (-t[0], t[1]))
    parts = [f"{_phrase(k, r)} (+{v:.2f})" for v, k in contrib[:WHY_MAX_PARTS] if v >= WHY_MIN_CONTRIB]
    if not parts:
        parts = ["слабые сигналы: " + _phrase(contrib[0][1], r)]
    if r.truncated:
        parts.append(f"4-е колено, исходящие не выгружались: ×{BOUNDARY_FACTOR}")
    elif r.is_seed:
        parts.append("seed: вход занижен выгрузкой, транзит не оценён")
    return "; ".join(parts)


# ---------------------------------------------------------------- расчёт

def run(edges: pd.DataFrame, nodes: pd.DataFrame, node_clusters: pd.DataFrame) -> pd.DataFrame:
    """Строка на каждый gid из nodes: gid, priority_score (0..1), why + вклад компонент.

    Отсортировано по убыванию priority_score, при равенстве — по возрастанию gid.
    node_clusters — результат analytics.clustering.run (колонки gid, cluster_id).
    """
    f = _features(edges, nodes, node_clusters)
    comp = _components(f)

    res = f.copy()
    res["flow_is_transit"] = comp.flow_is_transit
    for k, w in WEIGHTS.items():
        res[f"contrib_{k}"] = w * comp[k]
    res["boundary_factor"] = np.where(f.truncated, BOUNDARY_FACTOR, 1.0)
    raw = res[[f"contrib_{k}" for k in WEIGHTS]].sum(axis=1)
    res["priority_score"] = (raw * res.boundary_factor).clip(0, 1).round(SCORE_DECIMALS)

    res = res.reset_index()
    res["why"] = [_why(r) for r in res.itertuples(index=False)]
    for k in WEIGHTS:
        res[f"contrib_{k}"] = res[f"contrib_{k}"].round(SCORE_DECIMALS)
    return (res.sort_values(["priority_score", "gid"], ascending=[False, True])
               .reset_index(drop=True)[OUTPUT_COLUMNS])


# ---------------------------------------------------------------- проверка весов

def _top_gids(pr: pd.DataFrame, score: np.ndarray, top: int) -> set:
    order = np.lexsort((pr.gid.to_numpy(), -score))
    return set(pr.gid.to_numpy()[order[:top]])


def weight_sensitivity(pr: pd.DataFrame, top: int = 20, spread: float = 0.5,
                       n_runs: int = 1000, seed: int = 0) -> dict:
    """Насколько топ зависит от выбора весов.

    random — каждый вес умножается на U(1 - spread, 1 + spread), доля исходного
    топа, оставшаяся в топе (среднее, 5-й перцентиль, минимум по прогонам);
    drop — сколько узлов исходного топа остаётся, если компоненту убрать совсем.
    """
    keys = list(WEIGHTS)
    comp = np.column_stack([pr[f"contrib_{k}"] / WEIGHTS[k] for k in keys])
    factor = pr.boundary_factor.to_numpy()
    w0 = np.array([WEIGHTS[k] for k in keys])
    base = _top_gids(pr, comp @ w0 * factor, top)

    rng = np.random.default_rng(seed)
    overlap = np.array([
        len(base & _top_gids(pr, comp @ (w0 * rng.uniform(1 - spread, 1 + spread, len(keys))) * factor, top)) / top
        for _ in range(n_runs)])
    drop = {k: len(base & _top_gids(pr, comp @ np.where(np.arange(len(keys)) == i, 0, w0) * factor, top))
            for i, k in enumerate(keys)}
    return {"random_mean": float(overlap.mean()), "random_p05": float(np.quantile(overlap, 0.05)),
            "random_min": float(overlap.min()), "drop": drop}


def main():
    from analytics.clustering import load_inputs, run as run_clustering

    ap = argparse.ArgumentParser(description="priority_score для каждого узла")
    ap.add_argument("--data", default="data", help="папка с parquet-файлами")
    ap.add_argument("--out", default="out", help="куда писать priority.csv")
    a = ap.parse_args()

    edges, nodes = load_inputs(Path(a.data))
    node_clusters, _ = run_clustering(edges, nodes)
    pr = run(edges, nodes, node_clusters)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pr.to_csv(out / "priority.csv", index=False)
    print(f"узлов: {len(pr)}, priority_score: медиана {pr.priority_score.median():.3f}, "
          f"max {pr.priority_score.max():.3f}")
    print(pr.head(10)[["gid", "priority_score", "why"]].to_string(index=False))

    s = weight_sensitivity(pr)
    print(f"\nустойчивость топ-20 к весам ±50%: в среднем {s['random_mean']:.0%} топа сохраняется, "
          f"5-й перцентиль {s['random_p05']:.0%}, минимум {s['random_min']:.0%}")
    print("без компоненты остаётся из топ-20: "
          + ", ".join(f"{k} {v}" for k, v in s["drop"].items()))
    print(f"записано: {out / 'priority.csv'}")


if __name__ == "__main__":
    main()
