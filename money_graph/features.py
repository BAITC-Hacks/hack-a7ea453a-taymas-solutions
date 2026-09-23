"""Признаки узлов.

Базовые метрики перенесены из starter.py без изменения смысла. Поверх них:
  * уникальные плательщики / получатели, в том числе seed, и концентрация потока;
  * центральности на направленном графе: betweenness, HITS, охват вниз / вверх;
  * признак границы обхода: какие потоки узла вообще наблюдаемы в выгрузке;
  * временные паттерны по transactions.parquet: задержка вход→выход,
    синхронные поступления, всплески;
  * возвратные потоки: простые циклы длиной до CYCLE_MAX_LEN.
"""

from collections import Counter

import networkx as nx
import numpy as np
import pandas as pd

from . import config as C


def basic_features(G: nx.DiGraph, nodes: pd.DataFrame) -> pd.DataFrame:
    """Базовые метрики из starter.py."""
    in_deg = dict(G.in_degree())
    out_deg = dict(G.out_degree())
    in_kzt = dict(G.in_degree(weight="sum_kzt"))
    out_kzt = dict(G.out_degree(weight="sum_kzt"))
    in_tx = dict(G.in_degree(weight="n_tx"))
    out_tx = dict(G.out_degree(weight="n_tx"))
    pr = nx.pagerank(G, weight="sum_kzt")

    df = nodes[["gid", "depth", "is_seed"]].sort_values("gid").reset_index(drop=True)
    df["in_deg"] = df.gid.map(in_deg).fillna(0).astype(int)
    df["out_deg"] = df.gid.map(out_deg).fillna(0).astype(int)
    df["in_kzt"] = df.gid.map(in_kzt).fillna(0.0)
    df["out_kzt"] = df.gid.map(out_kzt).fillna(0.0)
    df["in_tx"] = df.gid.map(in_tx).fillna(0).astype(int)
    df["out_tx"] = df.gid.map(out_tx).fillna(0).astype(int)
    df["pagerank"] = df.gid.map(pr).fillna(0.0)

    # доля полученного, которая ушла дальше. Около 1.0 — деньги не задерживаются.
    df["pass_through"] = np.where(df.in_kzt > 0, df.out_kzt / df.in_kzt.replace(0, np.nan), np.nan)
    df["truncated_by_depth"] = (df.depth == C.MAX_DEPTH) & (df.out_deg == 0)
    return df


def counterparty_features(G: nx.DiGraph, df: pd.DataFrame) -> pd.DataFrame:
    """Уникальные плательщики / получатели и концентрация потока.

    in_deg в DiGraph уже равен числу уникальных плательщиков; здесь он дублируется
    говорящим именем и дополняется разбивкой по seed и долей крупнейшего контрагента.
    Средний чек разделяет «один перевод на 4 млн» и «40 переводов по 100 тыс.».
    """
    seeds = set(df.gid[df.is_seed])
    n_seed_payers, n_seed_receivers, top_payer_share, top_receiver_share = {}, {}, {}, {}
    for v in G.nodes:
        preds, succs = list(G.predecessors(v)), list(G.successors(v))
        n_seed_payers[v] = sum(p in seeds for p in preds)
        n_seed_receivers[v] = sum(s in seeds for s in succs)
        in_w = [G[p][v]["sum_kzt"] for p in preds]
        out_w = [G[v][s]["sum_kzt"] for s in succs]
        top_payer_share[v] = max(in_w) / sum(in_w) if in_w else np.nan
        top_receiver_share[v] = max(out_w) / sum(out_w) if out_w else np.nan

    df["n_payers"] = df.in_deg
    df["n_receivers"] = df.out_deg
    df["n_seed_payers"] = df.gid.map(n_seed_payers).fillna(0).astype(int)
    df["n_seed_receivers"] = df.gid.map(n_seed_receivers).fillna(0).astype(int)
    df["top_payer_share"] = df.gid.map(top_payer_share)
    df["top_receiver_share"] = df.gid.map(top_receiver_share)
    df["avg_in_tx_kzt"] = np.where(df.in_tx > 0, df.in_kzt / df.in_tx.replace(0, np.nan), 0.0)
    df["avg_out_tx_kzt"] = np.where(df.out_tx > 0, df.out_kzt / df.out_tx.replace(0, np.nan), 0.0)
    return df


def hits_power(G: nx.DiGraph, max_iter: int = 1000, tol: float = 1e-12):
    """HITS степенным методом с фиксированным стартом (все единицы).

    nx.hits в networkx 3.6 считает через scipy svds со случайным стартовым
    вектором — результат плавает от запуска к запуску. Здесь то же определение
    (hub = A·auth, auth = Aᵀ·hub, нормировка на сумму), но детерминированно.
    """
    nodelist = list(G.nodes)
    A = nx.to_scipy_sparse_array(G, nodelist=nodelist, weight=None, dtype=float)
    h = np.ones(len(nodelist))
    for _ in range(max_iter):
        a = A.T @ h
        a /= a.sum() or 1.0
        h_new = A @ a
        h_new /= h_new.sum() or 1.0
        done = np.abs(h_new - h).sum() < tol
        h = h_new
        if done:
            break
    a = A.T @ h
    a /= a.sum() or 1.0
    return dict(zip(nodelist, h)), dict(zip(nodelist, a))


def centrality_features(G: nx.DiGraph, df: pd.DataFrame) -> pd.DataFrame:
    """Центральности. Все считаются на НАПРАВЛЕННОМ графе.

    * betweenness — доля кратчайших направленных путей, проходящих через узел
      («через кого идут деньги»); точный расчёт без сэмплирования → детерминирован;
    * hub / authority (HITS) — «отправляет тем, кто собирает» / «собирает от тех,
      кто рассылает»; хорошо разделяет источники и точки сбора;
    * downstream_reach — сколько узлов достижимо по направлению денег;
    * n_seed_upstream — от скольких seed деньги могут дойти до узла.
    """
    btw = nx.betweenness_centrality(G, normalized=True)
    hubs, auth = hits_power(G)
    seeds = set(df.gid[df.is_seed])
    reach = {v: len(nx.descendants(G, v)) for v in G.nodes}
    seed_up = {v: len(nx.ancestors(G, v) & seeds) for v in G.nodes}

    df["betweenness"] = df.gid.map(btw).fillna(0.0)
    df["hub_score"] = df.gid.map(hubs).fillna(0.0)
    df["authority_score"] = df.gid.map(auth).fillna(0.0)
    df["downstream_reach"] = df.gid.map(reach).fillna(0).astype(int)
    df["n_seed_upstream"] = df.gid.map(seed_up).fillna(0).astype(int)

    wcc = sorted(nx.weakly_connected_components(G), key=lambda c: (-len(c), min(c)))
    comp_id = {v: i for i, comp in enumerate(wcc) for v in comp}
    comp_size = {v: len(comp) for comp in wcc for v in comp}
    df["wcc_id"] = df.gid.map(comp_id).astype(int)
    df["wcc_size"] = df.gid.map(comp_size).astype(int)
    return df


def boundary_features(df: pd.DataFrame) -> pd.DataFrame:
    """Признак границы обхода — какие потоки узла видны в выгрузке.

    Граф собран обходом исходящих от seed на MAX_DEPTH колен, поэтому:
      * depth < MAX_DEPTH  → исходящие узла выгружены полностью (рёбра 4-го колена
        как раз исходят из узлов depth=3), out_deg = 0 здесь — наблюдаемый факт;
      * depth = MAX_DEPTH  → boundary_depth4: исходящие не выгружались вообще, out_deg = 0 ничего
        не говорит о том, осели ли деньги (ловушка 1);
      * is_seed            → входящие извне выборки не видны, in_kzt занижен
        и pass_through некорректен (ловушка 2).
    boundary — одно из: full / out_hidden / in_hidden / isolated.
    """
    df["boundary_depth4"] = df.depth == C.MAX_DEPTH     # граница обхода: исходящие не выгружались
    df["out_observable"] = ~df.boundary_depth4
    df["in_underestimated"] = df.is_seed
    df["boundary"] = np.select(
        [(df.in_deg == 0) & (df.out_deg == 0), ~df.out_observable, df.in_underestimated],
        ["isolated", "out_hidden", "in_hidden"],
        default="full",
    )
    # отдаёт заметно больше, чем получил в графе → есть источник вне выгрузки
    df["external_inflow_suspected"] = (~df.is_seed) & (df.out_kzt > C.EXTERNAL_INFLOW_RATIO * df.in_kzt)
    # pass_through, которому можно доверять: только не-seed с наблюдаемым входом и выходом
    df["pass_through_reliable"] = np.where(
        (~df.is_seed) & df.out_observable & (df.in_kzt > 0), df.pass_through, np.nan)
    return df


def temporal_features(tx: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Временные паттерны по отдельным транзакциям.

    * fast_out_share — доля исходящей суммы, ушедшей не позже FAST_TRANSIT_DAYS дней
      после ближайшего предшествующего поступления (сквозной транзит);
    * median_lag_days — медианная задержка «последнее поступление → перевод дальше»;
    * sync_payers_max — максимум разных плательщиков, заплативших узлу в один день;
    * max_tx_per_day, active_days — всплески активности.
    """
    t = tx[["src", "dst", "date", "sum_kzt"]].copy()
    inc = t.rename(columns={"dst": "gid", "src": "cp"})
    out = t.rename(columns={"src": "gid", "dst": "cp"})

    # задержка: для каждого исходящего перевода — последнее поступление в тот же день или раньше
    o = out.sort_values(["date", "gid", "cp", "sum_kzt"]).reset_index(drop=True)
    i = inc[["gid", "date"]].drop_duplicates().rename(columns={"date": "in_date"}).sort_values("in_date")
    m = pd.merge_asof(o, i, left_on="date", right_on="in_date", by="gid", direction="backward")
    m["lag"] = (m.date - m.in_date).dt.days
    m["fast"] = m.lag <= C.FAST_TRANSIT_DAYS
    m["fast_kzt"] = np.where(m.fast, m.sum_kzt, 0.0)
    lag = m.groupby("gid").agg(fast_kzt=("fast_kzt", "sum"), out_sum=("sum_kzt", "sum"),
                               median_lag_days=("lag", "median"), n_with_prior_in=("in_date", "count"))
    # если ни одному исходящему переводу не предшествовало поступление (узел без входящих
    # или всё отправил раньше, чем получил), скорость транзита не определена — NaN, а не 0
    lag["fast_out_share"] = (lag.fast_kzt / lag.out_sum).where(lag.n_with_prior_in > 0)

    sync = inc.groupby(["gid", "date"]).cp.nunique().groupby("gid").max().rename("sync_payers_max")
    both = pd.concat([inc[["gid", "date"]], out[["gid", "date"]]])
    burst = both.groupby(["gid", "date"]).size().groupby("gid").max().rename("max_tx_per_day")
    days = both.groupby("gid").date.nunique().rename("active_days")

    df["fast_out_share"] = df.gid.map(lag.fast_out_share)          # NaN: нет входа до выхода
    df["median_lag_days"] = df.gid.map(lag.median_lag_days)
    df["sync_payers_max"] = df.gid.map(sync).fillna(0).astype(int)
    df["max_tx_per_day"] = df.gid.map(burst).fillna(0).astype(int)
    df["active_days"] = df.gid.map(days).fillna(0).astype(int)
    return df


def cycle_features(G: nx.DiGraph, df: pd.DataFrame) -> pd.DataFrame:
    """Возвратные потоки: простые направленные циклы длиной 2..CYCLE_MAX_LEN.

    n_cycles — во скольких циклах участвует узел; min_cycle_len — длина самого
    короткого (2 = взаимные переводы A⇄B, 3+ = деньги возвращаются через посредников).
    """
    cnt, shortest = Counter(), {}
    for cyc in nx.simple_cycles(G, length_bound=C.CYCLE_MAX_LEN):
        for v in cyc:
            cnt[v] += 1
            shortest[v] = min(shortest.get(v, 99), len(cyc))
    df["n_cycles"] = df.gid.map(cnt).fillna(0).astype(int)
    df["min_cycle_len"] = df.gid.map(shortest).fillna(0).astype(int)
    return df


def build_features(G: nx.DiGraph, nodes: pd.DataFrame, tx: pd.DataFrame) -> pd.DataFrame:
    df = basic_features(G, nodes)
    df = counterparty_features(G, df)
    df = centrality_features(G, df)
    df = boundary_features(df)
    df = temporal_features(tx, df)
    df = cycle_features(G, df)
    return df
