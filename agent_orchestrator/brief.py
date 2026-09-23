"""Compose only sourced facts. Neither CSV prose nor model prose is executable."""

import math

from .contracts import Answer, MAX_ITEMS, Request, parse_gid

ROLES = {"consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"}
NODE_FIELDS = ("role", "priority_score", "role_score", "in_kzt", "out_kzt", "in_tx", "out_tx",
               "n_payers", "n_receivers", "depth", "is_seed", "contrib_collect", "contrib_fanout",
               "contrib_flow", "contrib_seed", "contrib_bridge", "contrib_volume", "boundary_factor")
NEXT_STEPS = {
    "statements": "Запросить выписки по указанным gid и сверить видимые входящие и исходящие переводы.",
    "payers": "Сопоставить плательщиков указанных gid, даты переводов и основания платежей.",
    "boundary": "Запросить продолжение исходящих переводов за границей обхода; отсутствие рёбер не доказывает оседание денег.",
}
WARNING_TEXT = {
    "depth4_outflow_unobserved": "4-е колено: исходящие не выгружались; отсутствие оттока не доказывает оседание денег.",
    "seed_inflow_underestimated": "Входящие seed извне выборки не наблюдаются: видимая сумма входа неполная.",
    "external_inflow": "Видимый вход не покрывает отток; вероятен источник вне выгрузки.",
    "partial_results": "Показана ограниченная часть связей и кандидатов; отсутствие в списке не означает отсутствие в графе.",
    "sample_limits": "Выгрузка ограничена исходящим обходом от seed и порогом 5000 KZT; выводы относятся только к наблюдаемому графу.",
}


def empty_answer(question: str, intent="other", *, code: str, message: str) -> Answer:
    return {"question": question, "intent": intent, "provider": "fallback", "status": "error",
            "summary": message + " Любой аналитический вывод — гипотеза для проверки.",
            "gids": [], "claims": [], "candidates": [], "evidence": [], "sources": [],
            "tool_calls": [], "warnings": [], "next_steps": [], "fallback_reason": code,
            "error": {"code": code, "message": message}, "verification": "not_applicable"}


def compose(request: Request, intent: str, calls: list[dict], results: list[dict]) -> Answer:
    nodes, edges, candidates = {}, {}, []
    truncated = False
    collectors_found = False
    for result in results:
        rows = ([result["node"]] if "node" in result else [])
        for key in ("nodes", "candidates", "collectors"):
            rows += result.get(key, [])
        if "collectors" in result:
            candidates = [parse_gid(n["gid"]) for n in result["collectors"]]
            collectors_found = bool(candidates)
            for collector in result["collectors"]:
                for payer in collector.get("sources_reached", []):
                    if payer.get("direct_sum_kzt") is not None:
                        edge = {"src": payer["gid"], "dst": collector["gid"],
                                "sum_kzt": payer["direct_sum_kzt"], "n_tx": payer["direct_n_tx"]}
                        edges[(parse_gid(edge["src"]), parse_gid(edge["dst"]))] = edge
        for node in rows:
            gid = parse_gid(node["gid"])
            nodes.setdefault(gid, {}).update(node)
        for edge in result.get("edges", []) + result.get("direct_links", []):
            edges[(parse_gid(edge["src"]), parse_gid(edge["dst"]))] = edge
            if "counterparty" in edge:
                node = edge["counterparty"]
                nodes.setdefault(parse_gid(node["gid"]), {}).update(node)
        truncated |= bool(result.get("truncated"))

    claims, warnings = [], [{"code": "sample_limits", "message": WARNING_TEXT["sample_limits"]}]

    def add_claim(kind, ids, field, value):
        if value is None:
            return
        if field == "role":
            if value not in ROLES:
                raise ValueError("invalid role")
        elif field == "is_seed":
            if type(value) is not bool:
                raise ValueError("invalid boolean")
        elif type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid number")
        if field in ("priority_score", "role_score") and not 0 <= value <= 1:
            raise ValueError("invalid score")
        source = {"file": "nodes_roles.csv" if kind == "node" else "edge_table.csv", **ids, "column": field}
        claims.append({"kind": kind, **ids, "field": field, "value": value, "source": source})

    for gid, node in sorted(nodes.items()):
        if not {"role", "priority_score", "in_kzt", "out_kzt", "in_tx", "out_tx", "depth", "is_seed"} <= node.keys():
            raise ValueError("incomplete node")
        for field in NODE_FIELDS:
            if field in node:
                add_claim("node", {"gid": gid}, field, node[field])
        codes = []
        if node.get("depth") == 4:
            if node.get("role") == "terminal":
                raise ValueError("terminal at boundary")
            codes += ["depth4_outflow_unobserved"]
        if node.get("is_seed"):
            codes += ["seed_inflow_underestimated"]
        if node.get("external_inflow_suspected"):
            codes += ["external_inflow"]
        for code in codes:
            warnings.append({"code": code, "gid": gid, "message": WARNING_TEXT[code]})
    for (src, dst), edge in sorted(edges.items()):
        for field in ("sum_kzt", "n_tx"):
            add_claim("edge", {"src": src, "dst": dst}, field, edge.get(field))
    if truncated:
        warnings.append({"code": "partial_results", "message": WARNING_TEXT["partial_results"]})

    if intent != "common_collector":
        pool = [g for g in request.selected_gids if g in nodes] or list(nodes)
        candidates = sorted(pool, key=lambda g: (-nodes[g].get("priority_score", 0), g))[:MAX_ITEMS]
    status = "ok" if candidates else "empty"
    if intent == "common_collector":
        summary = ("Найдены кандидаты на общего сборщика в пределах выбранной глубины. " if collectors_found else
                   "Общий сборщик в пределах выбранной глубины не наблюдается; более длинные пути не исключены. ")
    elif intent == "trace":
        summary = "Показаны наблюдаемые направления переводов выбранного узла. "
    elif intent == "next_step":
        summary = "Очередность проверки следует сохранённому priority_score. "
    else:
        summary = "Роль и приоритет основаны на наблюдаемых потоках. "
    if candidates:
        node = nodes[candidates[0]]
        summary += (f"gid {candidates[0]}: роль {node['role']}, priority_score {node['priority_score']}; "
                    f"вход {node['in_kzt']} KZT / {node['in_tx']} переводов, "
                    f"выход {node['out_kzt']} KZT / {node['out_tx']} переводов. ")
        components = {"contrib_collect": "сбор", "contrib_fanout": "рассылка", "contrib_flow": "характер потока",
                      "contrib_seed": "связь с seed", "contrib_bridge": "связь кластеров", "contrib_volume": "объём"}
        strongest = sorted((f for f in components if node.get(f, 0) > 0), key=lambda f: (-node[f], f))[:3]
        if strongest:
            summary += "Основные слагаемые приоритета до поправки границы: " + ", ".join(
                f"{components[f]} {node[f]}" for f in strongest) + ". "
            if node.get("boundary_factor") is not None:
                summary += f"Множитель границы {node['boundary_factor']}. "
    summary += "Это гипотеза для проверки."
    gids = sorted(set(nodes) | {g for pair in edges for g in pair})
    step = "boundary" if any(w["code"] == "depth4_outflow_unobserved" for w in warnings) else (
        "payers" if intent == "common_collector" else "statements")
    return {"question": request.question, "intent": intent, "provider": "fallback", "status": status,
            "summary": summary, "gids": gids, "claims": claims,
            "candidates": [{"gid": g, "role": nodes[g]["role"], "priority_score": nodes[g]["priority_score"]}
                           for g in candidates],
            "evidence": [{"claim_index": i} for i in range(len(claims))],
            "sources": [c["source"] for c in claims], "tool_calls": calls, "warnings": warnings,
            "next_steps": [NEXT_STEPS[step]], "fallback_reason": None, "error": None,
            "verification": "unavailable"}
