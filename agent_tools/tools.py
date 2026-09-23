"""Read-only инструменты графа для AML-агента (PAN-45).

Каждый инструмент:
  * работает без LLM и сети — только по выгрузкам пайплайна (GraphStore);
  * возвращает JSON-serializable dict; числа берутся из CSV без пересчёта;
  * детерминирован: списки отсортированы по score / сумме, при равенстве — по gid;
  * несёт `sources` — ссылки на файл, gid или ребро и колонки, откуда взят каждый факт
    (по ним evidence verifier PAN-47 сверяет ответ агента);
  * несёт `warnings` — границы выгрузки: 4-е колено (исходящие не выгружались),
    заниженный вход seed, источник денег вне выгрузки;
  * ограничивает размер ответа параметром limit (контекст LLM не раздувается).
"""

from collections import deque

import numpy as np

from .errors import InvalidArgumentError
from .store import GraphStore, parse_gid, py

MAX_LIMIT = 200
MAX_DEPTH = 4                 # глубина обхода в выгрузке — дальше данных нет

BRIEF_FIELDS = ["gid", "role", "role_score", "priority_score", "cluster_id", "depth", "is_seed",
                "boundary", "in_kzt", "out_kzt", "in_tx", "out_tx", "n_payers", "n_receivers"]
DETAIL_FIELDS = BRIEF_FIELDS + [
    "role_rule", "evidence", "priority_why", "n_seed_payers", "n_seed_receivers",
    "pass_through_reliable", "external_inflow_suspected", "fast_out_share", "sync_payers_max",
    "betweenness", "pagerank", "downstream_reach", "n_seed_upstream", "n_cycles",
    "contrib_collect", "contrib_fanout", "contrib_flow", "contrib_seed", "contrib_bridge",
    "contrib_volume", "boundary_factor"]
EDGE_FIELDS = ["src", "dst", "sum_kzt", "n_tx", "avg_tx_kzt", "max_tx_kzt", "n_repeat_tx",
               "first_date", "last_date", "active_days", "is_reciprocal", "is_back_edge",
               "share_of_src_out", "share_of_dst_in"]
RANK_FILTERS = {"role", "cluster_id", "is_seed", "depth", "boundary", "min_priority",
                "min_in_kzt", "min_out_kzt", "min_payers", "min_receivers", "exclude_gids"}


# ---------------------------------------------------------------- помощники

def _limit(value, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidArgumentError(f"limit должен быть целым ≥ 1, получено {value!r}")
    return min(value, MAX_LIMIT)


def _depth(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_DEPTH:
        raise InvalidArgumentError(f"depth должен быть целым от 1 до {MAX_DEPTH}, получено {value!r}")
    return value


def _gid_list(values, name: str, lo: int = 1, hi: int = 50) -> list[int]:
    if not isinstance(values, (list, tuple)):
        raise InvalidArgumentError(f"{name}: нужен список gid, получено {type(values).__name__}")
    gids = list(dict.fromkeys(parse_gid(v) for v in values))      # без дубликатов, порядок сохранён
    if not lo <= len(gids) <= hi:
        raise InvalidArgumentError(f"{name}: нужно от {lo} до {hi} разных gid, получено {len(gids)}")
    return gids


def _pick(row, fields) -> dict:
    return {f: py(row[f]) for f in fields if f in row.index}


def _node_source(gid: int, fields) -> dict:
    return {"file": "nodes_roles.csv", "gid": gid, "columns": [f for f in fields if f != "gid"]}


def _edge_source(src: int, dst: int) -> dict:
    return {"file": "edge_table.csv", "src": src, "dst": dst, "columns": ["sum_kzt", "n_tx"]}


def node_warnings(row) -> list[dict]:
    """Границы выгрузки, о которых агент обязан сказать."""
    gid = int(row["gid"])
    w = []
    if row["in_deg"] == 0 and row["out_deg"] == 0:
        w.append({"code": "no_transfers", "gid": gid,
                  "message": "нет переводов ≥5 000 KZT внутри банка за период: связей в выгрузке нет"})
    if not bool(row["out_observable"]):
        w.append({"code": "depth4_outflow_unobserved", "gid": gid,
                  "message": "4-е колено: исходящие переводы не выгружались; отсутствие оттока "
                             "не означает, что деньги осели (не terminal)"})
    if bool(row["is_seed"]):
        w.append({"code": "seed_inflow_underestimated", "gid": gid,
                  "message": "seed: входящие извне выборки не видны, in_kzt занижен"})
    if bool(row.get("external_inflow_suspected", False)):
        w.append({"code": "external_inflow", "gid": gid,
                  "message": "отдал больше 1.2 × видимого входа: вероятен источник денег вне выгрузки"})
    return w


class GraphTools:
    """Набор инструментов над одним GraphStore. Методы = разрешённые инструменты агента."""

    def __init__(self, store: GraphStore):
        self.store = store

    # ---------------------------------------------------------------- узлы

    def _brief(self, gid: int) -> dict:
        return _pick(self.store.nodes.loc[gid], BRIEF_FIELDS)

    def get_node(self, gid) -> dict:
        """Карточка узла: роль, скоры, потоки, evidence, разбор приоритета, предупреждения."""
        row = self.store.node(gid)
        g = int(row["gid"])
        node = _pick(row, DETAIL_FIELDS)
        top = self.store.top[self.store.top.gid == g]
        node["top_rank"] = int(top["rank"].iat[0]) if len(top) else None
        sources = [_node_source(g, [f for f in DETAIL_FIELDS if f in row.index])]
        if len(top):
            sources.append({"file": "top_nodes.csv", "gid": g, "columns": ["rank"]})
        return {"node": node, "warnings": node_warnings(row), "sources": sources}

    def _edges(self, gid, direction: str, limit) -> dict:
        row = self.store.node(gid)
        g = int(row["gid"])
        limit = _limit(limit, 50)
        e = self.store.in_edges(g) if direction == "in" else self.store.out_edges(g)
        cp_col = "src" if direction == "in" else "dst"
        e = e.sort_values(["sum_kzt", cp_col], ascending=[False, True])
        items = []
        for _, r in e.head(limit).iterrows():
            item = _pick(r, EDGE_FIELDS)
            item["counterparty"] = self._brief(int(r[cp_col]))
            items.append(item)
        totals = {"n_edges": int(len(e)), "sum_kzt": round(float(e.sum_kzt.sum()), 2), "n_tx": int(e.n_tx.sum())}
        node_cols = ["in_deg", "in_kzt", "in_tx"] if direction == "in" else ["out_deg", "out_kzt", "out_tx"]
        warnings = node_warnings(row)
        if direction == "in":
            warnings.append({"code": "inflow_sample_only", "gid": g,
                             "message": "видны только переводы от клиентов выборки: реальных плательщиков может быть больше"})
        return {
            "gid": g, "direction": direction, "node": self._brief(g), "totals": totals,
            "edges": items, "total_count": int(len(e)), "truncated": len(e) > limit,
            "warnings": warnings,
            "sources": [_node_source(g, node_cols)] + [_edge_source(i["src"], i["dst"]) for i in items],
        }

    def get_incoming(self, gid, limit: int | None = None) -> dict:
        """Кто платил узлу: рёбра по убыванию суммы, итоги сходятся с in_kzt / in_tx."""
        return self._edges(gid, "in", limit)

    def get_outgoing(self, gid, limit: int | None = None) -> dict:
        """Кому платил узел: рёбра по убыванию суммы, итоги сходятся с out_kzt / out_tx."""
        return self._edges(gid, "out", limit)

    # ---------------------------------------------------------------- обходы

    def _bfs(self, starts: list[int], depth: int, forward: bool) -> dict[int, int]:
        """Минимальное число шагов от ближайшего стартового узла."""
        step = self.store.successors if forward else self.store.predecessors
        dist = {g: 0 for g in starts}
        q = deque(starts)
        while q:
            v = q.popleft()
            if dist[v] == depth:
                continue
            for u in sorted(step(v)):
                if u not in dist:
                    dist[u] = dist[v] + 1
                    q.append(u)
        return dist

    def _trace(self, starts: list[int], depth: int, forward: bool, limit: int) -> dict:
        dist = self._bfs(starts, depth, forward)
        found = [g for g in dist if dist[g] > 0]
        prio = self.store.nodes.priority_score
        found.sort(key=lambda g: (dist[g], -prio[g], g))
        nodes = [{**self._brief(g), "distance": dist[g]} for g in found[:limit]]
        shown = set(starts) | {n["gid"] for n in nodes}
        e = self.store.edges
        sub = e[e.src.isin(shown) & e.dst.isin(shown)]
        # только рёбра, идущие по направлению обхода (к следующему шагу)
        near, far = (sub.src, sub.dst) if forward else (sub.dst, sub.src)
        # явная булева маска: пустой список pandas понял бы как выбор колонок
        step = np.array([dist.get(int(f), -1) == dist.get(int(n), -9) + 1 for n, f in zip(near, far)], dtype=bool)
        sub = sub.loc[step]
        sub = sub.sort_values(["sum_kzt", "src", "dst"], ascending=[False, True, True]).head(limit)
        edges = [_pick(r, ["src", "dst", "sum_kzt", "n_tx", "first_date", "last_date"]) for _, r in sub.iterrows()]

        warnings = []
        for g in starts:
            warnings += node_warnings(self.store.nodes.loc[g])
        if forward:
            frontier = sorted(g for g in dist if not bool(self.store.nodes.at[g, "out_observable"]) and g not in starts)
            if frontier:
                warnings.append({"code": "depth4_frontier", "gids": frontier[:20], "count": len(frontier),
                                 "message": f"{len(frontier)} узл. на 4-м колене: их исходящие не выгружались, "
                                            "цепочка может продолжаться за пределами данных"})
        else:
            warnings.append({"code": "upstream_partial",
                             "message": "граф собран по исходящим от seed: плательщики вне выборки не видны, "
                                        "цепочка вверх может продолжаться за пределами данных"})
        seeds = sorted(g for g in found if bool(self.store.nodes.at[g, "is_seed"]))
        return {
            "start": starts, "depth": depth, "direction": "downstream" if forward else "upstream",
            "nodes": nodes, "edges": edges, "total_count": len(found), "truncated": len(found) > limit,
            "seeds_reached": seeds, "warnings": warnings,
            "sources": [_node_source(n["gid"], BRIEF_FIELDS) for n in nodes]
                       + [_edge_source(x["src"], x["dst"]) for x in edges],
        }

    def trace_upstream(self, gids, depth: int = MAX_DEPTH, limit: int | None = None) -> dict:
        """Откуда пришли деньги: предки узлов до depth шагов против направления переводов."""
        starts = [int(self.store.node(g)["gid"]) for g in _gid_list(gids, "gids")]
        return self._trace(starts, _depth(depth), forward=False, limit=_limit(limit, 100))

    def trace_downstream(self, gid, depth: int = MAX_DEPTH, limit: int | None = None) -> dict:
        """Куда ушли деньги: потомки узла до depth шагов по направлению переводов."""
        start = int(self.store.node(gid)["gid"])
        return self._trace([start], _depth(depth), forward=True, limit=_limit(limit, 100))

    # ---------------------------------------------------------------- сборщики и рейтинг

    def find_common_collectors(self, gids, depth: int = 2, min_sources: int = 2,
                               limit: int | None = None) -> dict:
        """Кто собирает деньги с нескольких из заданных gid (напрямую или через посредников).

        Для каждого кандидата: сколько заданных gid до него доходят, за сколько шагов и
        сколько пришло напрямую. Суммы через посредников не складываются — по пути деньги
        смешиваются с чужими, поэтому показываются только прямые переводы.
        """
        sources = [int(self.store.node(g)["gid"]) for g in _gid_list(gids, "gids", lo=2)]
        depth = _depth(depth)
        if isinstance(min_sources, bool) or not isinstance(min_sources, int) or not 2 <= min_sources <= len(sources):
            raise InvalidArgumentError(f"min_sources должен быть от 2 до {len(sources)}, получено {min_sources!r}")
        limit = _limit(limit, 20)

        reach: dict[int, dict[int, int]] = {}
        for s in sources:
            for v, d in self._bfs([s], depth, forward=True).items():
                if d > 0 and v not in sources:
                    reach.setdefault(v, {})[s] = d

        e = self.store.edges
        from_sources = e[e.src.isin(sources)]
        direct_by_dst = {int(d): grp for d, grp in from_sources.groupby("dst")}
        empty = from_sources.iloc[0:0]
        rows = []
        for v, by_src in reach.items():
            if len(by_src) < min_sources:
                continue
            direct = direct_by_dst.get(v, empty)
            per_source = []
            for s in sorted(by_src):
                d = direct[direct.src == s]
                per_source.append({"gid": s, "hops": by_src[s],
                                   "direct_sum_kzt": py(d.sum_kzt.sum()) if len(d) else None,
                                   "direct_n_tx": int(d.n_tx.sum()) if len(d) else None})
            rows.append({**self._brief(v), "n_sources": len(by_src), "sources_reached": per_source,
                         "direct_sum_kzt": round(float(direct.sum_kzt.sum()), 2),
                         "direct_n_tx": int(direct.n_tx.sum()),
                         "warnings": node_warnings(self.store.nodes.loc[v])})
        rows.sort(key=lambda r: (-r["n_sources"], -r["direct_sum_kzt"], -(r["priority_score"] or 0), r["gid"]))
        shown = rows[:limit]
        src_refs = [_node_source(r["gid"], BRIEF_FIELDS) for r in shown]
        for r in shown:
            src_refs += [_edge_source(p["gid"], r["gid"]) for p in r["sources_reached"] if p["direct_sum_kzt"] is not None]
        result = {"start": sources, "depth": depth, "min_sources": min_sources,
                  "collectors": shown, "total_count": len(rows), "truncated": len(rows) > limit,
                  "warnings": [w for s in sources for w in node_warnings(self.store.nodes.loc[s])],
                  "sources": src_refs}
        if not rows:
            result["message"] = (f"ни один узел не получает деньги минимум от {min_sources} из {len(sources)} "
                                 f"заданных gid в пределах {depth} шаг.")
        return result

    def rank_candidates(self, filters: dict | None = None, limit: int | None = None) -> dict:
        """Узлы по убыванию priority_score с фильтрами: role, cluster_id, is_seed, depth,
        boundary, min_priority, min_in_kzt, min_out_kzt, min_payers, min_receivers, exclude_gids."""
        filters = dict(filters or {})
        unknown = set(filters) - RANK_FILTERS
        if unknown:
            raise InvalidArgumentError(f"неизвестные фильтры {sorted(unknown)}; допустимы {sorted(RANK_FILTERS)}")
        n = self.store.nodes
        mask = n.gid.notna()

        def as_list(v):
            return v if isinstance(v, (list, tuple)) else [v]

        for col in ("role", "boundary"):
            if col in filters:
                mask &= n[col].isin(as_list(filters[col]))
        for col in ("cluster_id", "depth"):
            if col in filters:
                mask &= n[col].isin([int(x) for x in as_list(filters[col])])
        if "is_seed" in filters:
            mask &= n.is_seed == bool(filters["is_seed"])
        for key, col in (("min_priority", "priority_score"), ("min_in_kzt", "in_kzt"), ("min_out_kzt", "out_kzt"),
                         ("min_payers", "n_payers"), ("min_receivers", "n_receivers")):
            if key in filters:
                mask &= n[col] >= float(filters[key])
        if "exclude_gids" in filters:
            mask &= ~n.gid.isin([parse_gid(g) for g in as_list(filters["exclude_gids"])])

        limit = _limit(limit, 20)
        hit = n[mask].sort_values(["priority_score", "gid"], ascending=[False, True])
        fields = BRIEF_FIELDS + ["evidence", "priority_why"]
        cands = []
        for i, (_, r) in enumerate(hit.head(limit).iterrows(), start=1):
            cands.append({"rank": i, **_pick(r, fields), "warnings": node_warnings(r)})
        return {"filters": filters, "candidates": cands, "total_count": int(len(hit)),
                "truncated": len(hit) > limit,
                "sources": [_node_source(c["gid"], fields) for c in cands]}

    # ---------------------------------------------------------------- кластеры и сравнение

    def get_cluster(self, cluster_id, limit: int | None = None) -> dict:
        """Сводка кластера из clusters.csv и его участники по убыванию priority_score."""
        c = self.store.cluster(cluster_id)
        cid = int(c["cluster_id"])
        info = {k: py(v) for k, v in c.items()}
        info["top_gids"] = [int(x) for x in str(c["top_gids"]).split(";") if x.strip()]
        members = self.store.nodes[self.store.nodes.cluster_id == cid]
        limit = _limit(limit, 10)
        top = members.sort_values(["priority_score", "gid"], ascending=[False, True]).head(limit)
        roles = members.role.value_counts()
        warnings = []
        n4 = int((~members.out_observable.astype(bool)).sum())
        if n4:
            warnings.append({"code": "depth4_members", "count": n4,
                             "message": f"{n4} из {len(members)} участников на 4-м колене: их исходящие не выгружались"})
        return {
            "cluster": info,
            "members_count": int(len(members)),
            "roles": {r: int(roles[r]) for r in sorted(roles.index)},
            "top_members": [{**_pick(r, BRIEF_FIELDS + ["evidence"])} for _, r in top.iterrows()],
            "truncated": len(members) > limit,
            "warnings": warnings,
            "sources": [{"file": "clusters.csv", "cluster_id": cid, "columns": [k for k in info if k != "cluster_id"]}]
                       + [_node_source(int(g), BRIEF_FIELDS) for g in top.gid],
        }

    def compare_nodes(self, gids) -> dict:
        """Сравнение 2–10 узлов: метрики, прямые переводы между ними, общие плательщики и получатели."""
        ids = [int(self.store.node(g)["gid"]) for g in _gid_list(gids, "gids", lo=2, hi=10)]
        prio = self.store.nodes.priority_score
        ids.sort(key=lambda g: (-prio[g], g))
        fields = BRIEF_FIELDS + ["evidence", "priority_why"]
        rows = [{**_pick(self.store.nodes.loc[g], fields), "warnings": node_warnings(self.store.nodes.loc[g])}
                for g in ids]
        e = self.store.edges
        links = e[e.src.isin(ids) & e.dst.isin(ids)].sort_values(["sum_kzt", "src", "dst"], ascending=[False, True, True])
        direct = [_pick(r, ["src", "dst", "sum_kzt", "n_tx"]) for _, r in links.iterrows()]

        def shared(side_col, key_col):
            sub = e[e[key_col].isin(ids)]
            grp = sub.groupby(side_col)[key_col].apply(lambda s: sorted(set(int(x) for x in s)))
            out = [{"gid": int(g), "with": w} for g, w in grp.items() if len(w) >= 2 and int(g) not in ids]
            return sorted(out, key=lambda x: (-len(x["with"]), x["gid"]))

        return {
            "nodes": rows, "direct_links": direct,
            "shared_payers": shared("src", "dst"), "shared_receivers": shared("dst", "src"),
            "sources": [_node_source(g, fields) for g in ids] + [_edge_source(x["src"], x["dst"]) for x in direct],
        }


TOOL_NAMES = ("get_node", "get_incoming", "get_outgoing", "trace_upstream", "trace_downstream",
              "find_common_collectors", "rank_candidates", "get_cluster", "compare_nodes")
