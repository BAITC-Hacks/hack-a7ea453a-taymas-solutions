"""Контракт ответа агента (PAN-47) — то, что оркестратор PAN-46 возвращает в UI.

Верификатор проверяет не свободный текст, а явные утверждения: каждое число
или роль в ответе должно быть claim-ом со ссылкой на источник. Текст summary
строится только render_summary из этих claims и сравнивается с ним точно.
Произвольный текст, даже с известными числами, не считается проверенным.

    {
      "question": "Кто собирает деньги с этих пяти gid?",
      "intent": "common_collector",
      "provider": "fallback" | "nvidia" | "template",
      "summary": "… гипотеза для проверки …",
      "gids": [100000003115284100, ...],                 # все gid, упомянутые в ответе
      "claims": [
        {"kind": "node", "gid": 1000…, "field": "role", "value": "consolidator",
         "source": {"file": "nodes_roles.csv", "gid": 1000…, "column": "role"}},
        {"kind": "edge", "src": 1000…, "dst": 1000…, "field": "sum_kzt", "value": 815000.0,
         "source": {"file": "edge_table.csv", "src": 1000…, "dst": 1000…, "column": "sum_kzt"}},
        {"kind": "cluster", "cluster_id": 5, "field": "n_seed", "value": 3,
         "source": {"file": "clusters.csv", "cluster_id": 5, "column": "n_seed"}},
        {"kind": "hypothesis", "text": "гипотеза: …", "gids": [...]},     # вывод без прямого факта
        {"kind": "not_observed", "text": "не наблюдается в данных: …"}  # факта в выгрузке нет
      ],
      "tool_calls": [{"tool": "find_common_collectors", "args": {"gids": [...]}}],
      "warnings": [{"code": "depth4_outflow_unobserved", "gid": 1000…, "message": "…"}],
      "next_steps": ["запросить выписку по …"]
    }
"""

import math

INTENTS = ["priority", "explanation", "common_collector", "next_step", "upstream", "downstream",
           "trace", "depth4", "cluster", "compare", "other"]
PROVIDERS = ["fallback", "nvidia", "template"]
CLAIM_KINDS = ["node", "edge", "cluster", "hypothesis", "not_observed"]
SOURCE_FILE = {"node": "nodes_roles.csv", "edge": "edge_table.csv", "cluster": "clusters.csv"}
NEXT_STEPS = {
    "statements": "Запросить выписки по указанным gid и сверить видимые входящие и исходящие переводы.",
    "payers": "Сопоставить плательщиков указанных gid, даты переводов и основания платежей.",
    "boundary": "Запросить продолжение исходящих переводов за границей обхода; отсутствие рёбер не доказывает оседание денег.",
}

_GID = {"type": "integer"}
CLAIM = {"type": "object", "required": ["kind"],
         "properties": {"kind": {"enum": CLAIM_KINDS}, "gid": _GID, "src": _GID, "dst": _GID,
                        "cluster_id": {"type": "integer"}, "field": {"type": "string"},
                        "text": {"type": "string"}, "gids": {"type": "array", "items": _GID},
                        "source": {"type": "object", "required": ["file", "column"],
                                   "properties": {"file": {"type": "string"}, "column": {"type": "string"}}}}}
ANSWER_SCHEMA = {
    "type": "object",
    "required": ["question", "intent", "provider", "summary", "gids", "claims", "tool_calls", "warnings", "next_steps"],
    "properties": {
        "question": {"type": "string"}, "intent": {"enum": INTENTS}, "provider": {"enum": PROVIDERS},
        "summary": {"type": "string"}, "gids": {"type": "array", "items": _GID},
        "claims": {"type": "array", "items": CLAIM},
        "tool_calls": {"type": "array", "items": {"type": "object", "required": ["tool", "args"],
                                                  "properties": {"tool": {"type": "string"},
                                                                 "args": {"type": "object"}}}},
        "warnings": {"type": "array", "items": {"type": "object", "required": ["code", "message"]}},
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "status": {"enum": ["ok", "empty", "error"]},
    },
}


def node_claim(gid: int, field: str, value) -> dict:
    return {"kind": "node", "gid": gid, "field": field, "value": value,
            "source": {"file": "nodes_roles.csv", "gid": gid, "column": field}}


def edge_claim(src: int, dst: int, field: str, value) -> dict:
    return {"kind": "edge", "src": src, "dst": dst, "field": field, "value": value,
            "source": {"file": "edge_table.csv", "src": src, "dst": dst, "column": field}}


def cluster_claim(cluster_id: int, field: str, value) -> dict:
    return {"kind": "cluster", "cluster_id": cluster_id, "field": field, "value": value,
            "source": {"file": "clusters.csv", "cluster_id": cluster_id, "column": field}}


def _summary_value(value, field):
    if field == "role":
        if value not in {"consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"}:
            raise ValueError("invalid summary role")
        return value
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("invalid summary number")
    precision = 2 if "kzt" in field else 6
    return f"{value:,.{precision}f}".rstrip("0").rstrip(".").replace(",", " ")


def render_summary(answer: dict) -> str:
    """Bounded presentation of typed claims; CSV/model prose is never interpolated.

    Rendering is not verification: callers must still validate every claim and
    its source against the store. The verifier rejects any different summary.
    """
    nodes, edges = {}, {}
    for claim in answer["claims"]:
        kind = claim["kind"]
        if kind not in ("node", "edge"):
            continue
        key = claim["gid"] if kind == "node" else (claim["src"], claim["dst"])
        values = (nodes if kind == "node" else edges).setdefault(key, {})
        field, value = claim["field"], claim["value"]
        if field in values and values[field] != value:
            raise ValueError("conflicting summary claims")
        values[field] = value

    intent = answer["intent"]
    if intent == "common_collector":
        intro = ("Общий сборщик в пределах выбранной глубины не наблюдается; более длинные пути не исключены."
                 if answer.get("status") == "empty" else
                 "Кандидаты на общего сборщика требуют проверки по наблюдаемым связям.")
    elif intent == "trace":
        intro = "Показаны наблюдаемые направления переводов."
    else:
        intro = "Роль и приоритет основаны на наблюдаемых потоках."
    parts = [intro]
    candidates = answer.get("candidates", [])
    primary = candidates[0]["gid"] if candidates else next(iter(nodes), None)
    if primary is not None:
        node = nodes[primary]
        labels = {"role": "роль", "priority_score": "priority_score", "in_kzt": "вход",
                  "in_tx": "входящих переводов", "out_kzt": "выход", "out_tx": "исходящих переводов",
                  "n_payers": "плательщиков", "n_receivers": "получателей", "depth": "колено", "out_deg": "исходящих связей"}
        facts = [f"{label} {_summary_value(node[field], field)}" + (" KZT" if "kzt" in field else "")
                 for field, label in labels.items() if field in node]
        if facts:
            parts.append(f"gid {primary}: " + "; ".join(facts) + ".")
        components = {"contrib_collect": "сбор", "contrib_fanout": "рассылка", "contrib_flow": "характер потока",
                      "contrib_seed": "связь с seed", "contrib_bridge": "связь кластеров", "contrib_volume": "объём"}
        strongest = sorted((f for f in components if node.get(f, 0) > 0), key=lambda f: (-node[f], f))[:3]
        if strongest:
            parts.append("Основные слагаемые приоритета до поправки границы: " + ", ".join(
                f"{components[f]} {_summary_value(node[f], f)}" for f in strongest) + ".")
            if "boundary_factor" in node:
                parts.append(f"Множитель границы {_summary_value(node['boundary_factor'], 'boundary_factor')}.")
    for (src, dst), values in sorted(edges.items())[:2]:
        facts = [f"{label} {_summary_value(values[field], field)}" + (" KZT" if field == "sum_kzt" else "")
                 for field, label in (("sum_kzt", "сумма"), ("n_tx", "переводов")) if field in values]
        if facts:
            parts.append(f"Перевод {src} → {dst}: " + "; ".join(facts) + ".")
    parts.append("Это гипотеза для проверки.")
    return " ".join(parts)
