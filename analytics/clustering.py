"""
Кластеризация графа переводов и сводка clusters.csv (PAN-35).

Метод:
  1. Граф разбивается на слабосвязные компоненты (направление не учитывается —
     деньги между компонентами не ходят совсем).
  2. Компоненты меньше MIN_COMPONENT_FOR_LOUVAIN узлов — один кластер целиком.
     Узлы без рёбер (19 seed без переводов ≥ 5 000 KZT) — кластер из одного узла.
  3. Крупные компоненты делятся Louvain на НЕОРИЕНТИРОВАННОЙ проекции:
     вес ребра {u, v} = sum_kzt(u→v) + sum_kzt(v→u). Направление при поиске
     сообществ теряется намеренно — Louvain в networkx не работает с направленными
     весами; направление возвращается на этапе сводки (входящие/исходящие хабы).
  4. Устойчивость: разбиение повторяется с N_STABILITY_RUNS другими seed, для
     каждого кластера считается средний лучший Jaccard с кластерами этих прогонов.

Результат детерминирован: узлы и рёбра добавляются в отсортированном порядке,
random state Louvain зафиксирован, cluster_id нумеруются по обороту.

Запуск:
    python -m analytics.clustering --data data --out out
"""

import argparse
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
from networkx.algorithms.community import louvain_communities

RANDOM_STATE = 42
LOUVAIN_RESOLUTION = 1.0
MIN_COMPONENT_FOR_LOUVAIN = 30
N_STABILITY_RUNS = 10
TOP_GIDS_PER_CLUSTER = 5

# пороги для текстовой гипотезы — подобраны по распределениям на датасете
HUB_MIN_PARTNERS_IN = 5        # консолидация: минимум разных плательщиков внутри кластера
HUB_MIN_PARTNERS_OUT = 10      # веер: минимум разных получателей внутри кластера
LARGE_EDGE_QUANTILE = 0.90     # «крупные переводы»: средняя связь кластера ≥ этого квантиля по графу
TRANSIT_PT_RANGE = (0.8, 1.2)  # pass_through, при котором узел считается пропускающим
TRANSIT_MIN_SHARE = 0.20
TRUNCATED_MIN_SHARE = 0.50
MAX_HYPOTHESIS_SIGNALS = 3
LOW_STABILITY = 0.6            # ниже — к гипотезе добавляется оговорка о границах кластера


# ---------------------------------------------------------------- граф

def load_inputs(data_dir: Path):
    edges = pd.read_parquet(data_dir / "edges.parquet")
    nodes = pd.read_parquet(data_dir / "nodes.parquet")
    return edges, nodes


def build_graphs(edges: pd.DataFrame, nodes: pd.DataFrame):
    """Направленный граф + неориентированная проекция с суммарным весом пары."""
    G = nx.DiGraph()
    G.add_nodes_from(sorted(nodes.gid.tolist()))
    for r in edges.sort_values(["src", "dst"]).itertuples(index=False):
        G.add_edge(r.src, r.dst, sum_kzt=float(r.sum_kzt), n_tx=int(r.n_tx))

    UG = nx.Graph()
    UG.add_nodes_from(G.nodes)
    for u, v, d in G.edges(data=True):
        if UG.has_edge(u, v):
            UG[u][v]["weight"] += d["sum_kzt"]
        else:
            UG.add_edge(u, v, weight=d["sum_kzt"])
    return G, UG


# ---------------------------------------------------------------- разбиение

def _components(UG: nx.Graph):
    """Компоненты в детерминированном порядке: по убыванию размера, затем по min gid."""
    return sorted((frozenset(c) for c in nx.connected_components(UG)),
                  key=lambda c: (-len(c), min(c)))


def partition(UG: nx.Graph, seed: int = RANDOM_STATE):
    """Список (component_id, frozenset узлов) — каждый узел ровно в одном кластере."""
    parts = []
    for comp_id, comp in enumerate(_components(UG), start=1):
        if len(comp) < MIN_COMPONENT_FOR_LOUVAIN:
            parts.append((comp_id, comp))
            continue
        sub = UG.subgraph(sorted(comp))
        for c in louvain_communities(sub, weight="weight",
                                     resolution=LOUVAIN_RESOLUTION, seed=seed):
            parts.append((comp_id, frozenset(c)))
    return parts


def stability(parts, UG: nx.Graph):
    """Для каждого кластера — средний лучший Jaccard с кластерами других прогонов."""
    scores = np.zeros(len(parts))
    for s in range(1, N_STABILITY_RUNS + 1):
        other = [c for _, c in partition(UG, seed=RANDOM_STATE + s)]
        where = {g: c for c in other for g in c}
        for i, (_, c) in enumerate(parts):
            best = 0.0
            for cand in {where[g] for g in c}:
                best = max(best, len(c & cand) / len(c | cand))
            scores[i] += best
    return scores / N_STABILITY_RUNS


# ---------------------------------------------------------------- сводка

def _node_flows(G: nx.DiGraph) -> pd.DataFrame:
    df = pd.DataFrame({"gid": list(G.nodes)})
    df["in_kzt"] = df.gid.map(dict(G.in_degree(weight="sum_kzt")))
    df["out_kzt"] = df.gid.map(dict(G.out_degree(weight="sum_kzt")))
    df["out_deg"] = df.gid.map(dict(G.out_degree()))
    return df.set_index("gid")


def _kzt(x: float) -> str:
    return f"{x / 1e6:.1f} млн KZT" if x >= 1e6 else f"{x / 1e3:.0f} тыс. KZT"


def _hypothesis(s: dict) -> str:
    """Осторожная гипотеза из графовых признаков. Формулировки — «признаки», не выводы.
    Сигналы перечислены по убыванию значимости, в текст попадают первые три."""
    if s["n_edges_internal"] == 0:
        who = "seed-клиент" if s["n_seed"] else "узел"
        return (f"Гипотеза: изолированный {who} — переводов ≥5 000 KZT в выборке нет; "
                "связи не видны, нужен запрос входящих или другого периода")

    is_cons = s["hub_in_partners"] >= HUB_MIN_PARTNERS_IN
    is_fan = s["hub_out_partners"] >= HUB_MIN_PARTNERS_OUT
    parts = []
    if is_cons and is_fan and s["hub_in_gid"] == s["hub_out_gid"]:
        parts.append(f"признаки координирующего узла: {s['hub_in_gid']} собирает от "
                     f"{s['hub_in_partners']} участников и рассылает {s['hub_out_partners']}")
    else:
        if is_cons:
            parts.append(f"признаки консолидации: {s['hub_in_gid']} получает от "
                         f"{s['hub_in_partners']} участников, {_kzt(s['hub_in_kzt'])}")
        if is_fan:
            parts.append(f"признаки веерного распределения: {s['hub_out_gid']} отправляет "
                         f"{s['hub_out_partners']} получателям, {s['hub_out_share']:.0%} внутр. оборота")
    if s["n_seed"] >= 2:
        parts.append(f"объединяет {s['n_seed']} seed — возможна общая инфраструктура")
    if s["avg_edge_kzt"] >= s["large_edge_threshold"]:
        parts.append(f"крупные переводы: в среднем {_kzt(s['avg_edge_kzt'])} на связь "
                     f"(по графу {LARGE_EDGE_QUANTILE:.0%} связей меньше {_kzt(s['large_edge_threshold'])})")
    if s["transit_share"] >= TRANSIT_MIN_SHARE:
        parts.append(f"транзитный контур: {s['transit_share']:.0%} узлов передают дальше "
                     "80–120% полученного")
    if s["truncated_share"] >= TRUNCATED_MIN_SHARE:
        parts.append(f"граница обхода: {s['truncated_share']:.0%} узлов на 4-м колене "
                     "без исходящих — структура видна не полностью")
    if not parts:
        parts.append(f"цепочки без выраженных хабов: {s['n_nodes']} узлов, "
                     f"{s['n_edges_internal']} связей, у любого узла не больше "
                     f"{s['hub_in_partners']} плательщ. — вероятно периферия сети")

    text = "Гипотеза: " + "; ".join(parts[:MAX_HYPOTHESIS_SIGNALS])
    if s["stability"] < LOW_STABILITY:
        text += f" (границы кластера неустойчивы: Jaccard {s['stability']:.2f} между прогонами)"
    return text


def summarize(G: nx.DiGraph, nodes: pd.DataFrame, parts, stab) -> tuple:
    """(node_clusters, clusters) — назначение узлов и строка на кластер."""
    flows = _node_flows(G)
    depth = nodes.set_index("gid").depth
    is_seed = nodes.set_index("gid").is_seed

    large_edge = float(np.quantile([d["sum_kzt"] for _, _, d in G.edges(data=True)],
                                   LARGE_EDGE_QUANTILE))

    rows = []
    for (comp_id, members), st in zip(parts, stab):
        sub = G.subgraph(members)
        internal = [d for _, _, d in sub.edges(data=True)]
        kzt_int = sum(d["sum_kzt"] for d in internal)

        # вклад узла во внутренний оборот: сумма его входящих и исходящих внутри кластера
        w_in = dict(sub.in_degree(weight="sum_kzt"))
        w_out = dict(sub.out_degree(weight="sum_kzt"))
        weight = {g: w_in[g] + w_out[g] for g in members}
        top = sorted(members, key=lambda g: (-weight[g], g))[:TOP_GIDS_PER_CLUSTER]

        deg_in, deg_out = dict(sub.in_degree()), dict(sub.out_degree())
        hub_in = min(members, key=lambda g: (-deg_in[g], -w_in[g], g))
        hub_out = min(members, key=lambda g: (-deg_out[g], -w_out[g], g))

        f = flows.loc[sorted(members)]
        seeds = is_seed.loc[f.index]
        pt = f.out_kzt / f.in_kzt.replace(0, np.nan)
        # seed исключены: их входящие занижены устройством выгрузки
        transit = (~seeds) & pt.between(*TRANSIT_PT_RANGE) & (f.out_deg > 0)
        truncated = (depth.loc[f.index] == 4) & (f.out_deg == 0)

        cross_in = sum(d["sum_kzt"] for u, v, d in G.in_edges(members, data=True) if u not in members)
        cross_out = sum(d["sum_kzt"] for u, v, d in G.out_edges(members, data=True) if v not in members)

        rows.append({
            "members": members,
            "component_id": comp_id,
            "n_nodes": len(members),
            "n_seed": int(seeds.sum()),
            "n_edges_internal": len(internal),
            "n_tx_internal": sum(d["n_tx"] for d in internal),
            "sum_kzt_internal": round(kzt_int, 2),
            "sum_kzt_in_external": round(cross_in, 2),
            "sum_kzt_out_external": round(cross_out, 2),
            "top_gids": ";".join(str(g) for g in top),
            "hub_in_gid": hub_in,
            "hub_in_partners": deg_in[hub_in],
            "hub_in_kzt": round(w_in[hub_in], 2),
            "hub_out_gid": hub_out,
            "hub_out_partners": deg_out[hub_out],
            "hub_out_kzt": round(w_out[hub_out], 2),
            "hub_out_share": w_out[hub_out] / kzt_int if kzt_int else 0.0,
            "avg_edge_kzt": round(kzt_int / len(internal), 2) if internal else 0.0,
            "large_edge_threshold": large_edge,
            "transit_share": float(transit.mean()),
            "truncated_share": float(truncated.mean()),
            "min_depth": int(depth.loc[f.index].min()),
            "max_depth": int(depth.loc[f.index].max()),
            "stability": round(float(st), 3),
        })

    # cluster_id: по убыванию внутреннего оборота, затем размера, затем min gid
    rows.sort(key=lambda r: (-r["sum_kzt_internal"], -r["n_nodes"], min(r["members"])))
    assign = []
    for cid, r in enumerate(rows, start=1):
        r["cluster_id"] = cid
        r["hypothesis"] = _hypothesis(r)
        assign += [(g, cid, r["component_id"]) for g in r["members"]]

    clusters = pd.DataFrame(rows).drop(columns=["members", "large_edge_threshold"])
    for col in ("hub_out_share", "transit_share", "truncated_share"):
        clusters[col] = clusters[col].round(3)
    first = ["cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"]
    clusters = clusters[first + [c for c in clusters.columns if c not in first]]

    node_clusters = (pd.DataFrame(assign, columns=["gid", "cluster_id", "component_id"])
                     .sort_values("gid").reset_index(drop=True))
    return node_clusters, clusters


def run(edges: pd.DataFrame, nodes: pd.DataFrame):
    G, UG = build_graphs(edges, nodes)
    parts = partition(UG)
    return summarize(G, nodes, parts, stability(parts, UG))


def main():
    ap = argparse.ArgumentParser(description="Кластеризация графа и clusters.csv")
    ap.add_argument("--data", default="data", help="папка с parquet-файлами")
    ap.add_argument("--out", default="out", help="куда писать выгрузки")
    a = ap.parse_args()

    edges, nodes = load_inputs(Path(a.data))
    node_clusters, clusters = run(edges, nodes)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    clusters.to_csv(out / "clusters.csv", index=False)
    node_clusters.to_csv(out / "node_clusters.csv", index=False)

    multi_seed = int((clusters.n_seed >= 2).sum())
    print(f"кластеров: {len(clusters)} (с 2+ seed: {multi_seed}), "
          f"узлов покрыто: {len(node_clusters)} из {len(nodes)}")
    print(f"записано: {out / 'clusters.csv'}, {out / 'node_clusters.csv'}")


if __name__ == "__main__":
    main()
