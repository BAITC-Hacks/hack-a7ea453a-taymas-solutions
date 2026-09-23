"""Контракт ответа агента (PAN-47) — то, что оркестратор PAN-46 возвращает в UI.

Верификатор проверяет не свободный текст, а явные утверждения: каждое число
или роль в ответе должно быть claim-ом со ссылкой на источник. Текст summary
дополнительно проверяется: любое число в нём должно прослеживаться до данных.

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

INTENTS = ["priority", "explanation", "common_collector", "next_step", "upstream", "downstream",
           "trace", "depth4", "cluster", "compare", "other"]
PROVIDERS = ["fallback", "nvidia", "template"]
CLAIM_KINDS = ["node", "edge", "cluster", "hypothesis", "not_observed"]
SOURCE_FILE = {"node": "nodes_roles.csv", "edge": "edge_table.csv", "cluster": "clusters.csv"}

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
