"""JSON-схемы параметров (для function calling) и результатов инструментов.

PARAMETERS — формат `parameters` из OpenAI-совместимого tool calling (его принимает
NVIDIA API): оркестратор PAN-46 передаёт их модели как есть.
RESULTS — контракт ответа; `validate` проверяет его без внешних зависимостей.
"""

GID = {"type": ["integer", "string"], "description": "gid клиента: целое число или строка цифр (не float)"}
GIDS = {"type": "array", "items": GID}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 200}
DEPTH = {"type": "integer", "minimum": 1, "maximum": 4}

PARAMETERS = {
    "get_node": {"type": "object", "properties": {"gid": GID}, "required": ["gid"],
                 "additionalProperties": False},
    "get_incoming": {"type": "object", "properties": {"gid": GID, "limit": LIMIT}, "required": ["gid"],
                     "additionalProperties": False},
    "get_outgoing": {"type": "object", "properties": {"gid": GID, "limit": LIMIT}, "required": ["gid"],
                     "additionalProperties": False},
    "trace_upstream": {"type": "object", "properties": {"gids": GIDS, "depth": DEPTH, "limit": LIMIT},
                       "required": ["gids"], "additionalProperties": False},
    "trace_downstream": {"type": "object", "properties": {"gid": GID, "depth": DEPTH, "limit": LIMIT},
                         "required": ["gid"], "additionalProperties": False},
    "find_common_collectors": {"type": "object",
                               "properties": {"gids": GIDS, "depth": DEPTH,
                                              "min_sources": {"type": "integer", "minimum": 2}, "limit": LIMIT},
                               "required": ["gids"], "additionalProperties": False},
    "rank_candidates": {"type": "object",
                        "properties": {"filters": {"type": "object", "properties": {
                            "role": {"type": ["string", "array"]}, "cluster_id": {"type": ["integer", "array"]},
                            "is_seed": {"type": "boolean"}, "depth": {"type": ["integer", "array"]},
                            "boundary": {"type": ["string", "array"]}, "min_priority": {"type": "number"},
                            "min_in_kzt": {"type": "number"}, "min_out_kzt": {"type": "number"},
                            "min_payers": {"type": "integer"}, "min_receivers": {"type": "integer"},
                            "exclude_gids": GIDS}, "additionalProperties": False},
                            "limit": LIMIT},
                        "additionalProperties": False},
    "get_cluster": {"type": "object", "properties": {"cluster_id": {"type": "integer"}, "limit": LIMIT},
                    "required": ["cluster_id"], "additionalProperties": False},
    "compare_nodes": {"type": "object", "properties": {"gids": GIDS}, "required": ["gids"],
                      "additionalProperties": False},
}

DESCRIPTIONS = {
    "get_node": "Карточка клиента: роль, role_score, priority_score, кластер, потоки, evidence, предупреждения.",
    "get_incoming": "Кто переводил деньги клиенту: рёбра по убыванию суммы и итоги.",
    "get_outgoing": "Кому клиент переводил деньги: рёбра по убыванию суммы и итоги.",
    "trace_upstream": "Откуда пришли деньги к заданным клиентам: предки до depth шагов.",
    "trace_downstream": "Куда ушли деньги клиента: потомки до depth шагов.",
    "find_common_collectors": "Кто собирает деньги с нескольких заданных клиентов (напрямую или через посредников).",
    "rank_candidates": "Кого проверить первым: клиенты по убыванию priority_score с фильтрами.",
    "get_cluster": "Сводка кластера: размер, seed, оборот, гипотеза, главные участники.",
    "compare_nodes": "Сравнение 2–10 клиентов: метрики, прямые переводы, общие плательщики и получатели.",
}

_NUM = {"type": ["number", "null"]}
_INT = {"type": "integer"}
WARNING = {"type": "object", "required": ["code", "message"],
           "properties": {"code": {"type": "string"}, "message": {"type": "string"}}}
SOURCE = {"type": "object", "required": ["file", "columns"],
          "properties": {"file": {"enum": ["nodes_roles.csv", "edge_table.csv", "clusters.csv", "top_nodes.csv"]},
                         "columns": {"type": "array", "items": {"type": "string"}}}}
BRIEF = {"type": "object",
         "required": ["gid", "role", "role_score", "priority_score", "cluster_id", "depth", "is_seed",
                      "in_kzt", "out_kzt", "in_tx", "out_tx"],
         "properties": {"gid": _INT, "role": {"enum": ["consolidator", "transit", "distributor", "terminal",
                                                        "coordinator", "peripheral"]},
                        "role_score": {"type": "number"}, "priority_score": {"type": "number"},
                        "cluster_id": _INT, "depth": _INT, "is_seed": {"type": "boolean"},
                        "in_kzt": {"type": "number"}, "out_kzt": {"type": "number"},
                        "in_tx": _INT, "out_tx": _INT}}
EDGE = {"type": "object", "required": ["src", "dst", "sum_kzt", "n_tx"],
        "properties": {"src": _INT, "dst": _INT, "sum_kzt": {"type": "number"}, "n_tx": _INT}}
COMMON = {"warnings": {"type": "array", "items": WARNING}, "sources": {"type": "array", "items": SOURCE}}

_EDGES_RESULT = {"type": "object",
                 "required": ["gid", "direction", "node", "totals", "edges", "total_count", "truncated",
                              "warnings", "sources"],
                 "properties": {"gid": _INT, "node": BRIEF, "edges": {"type": "array", "items": EDGE},
                                "totals": {"type": "object", "required": ["n_edges", "sum_kzt", "n_tx"]},
                                "truncated": {"type": "boolean"}, **COMMON}}
_TRACE_RESULT = {"type": "object",
                 "required": ["start", "depth", "direction", "nodes", "edges", "total_count", "truncated",
                              "seeds_reached", "warnings", "sources"],
                 "properties": {"start": {"type": "array", "items": _INT},
                                "nodes": {"type": "array", "items": BRIEF},
                                "edges": {"type": "array", "items": EDGE}, **COMMON}}

RESULTS = {
    "get_node": {"type": "object", "required": ["node", "warnings", "sources"],
                 "properties": {"node": {**BRIEF, "required": BRIEF["required"] + ["evidence"]}, **COMMON}},
    "get_incoming": _EDGES_RESULT,
    "get_outgoing": _EDGES_RESULT,
    "trace_upstream": _TRACE_RESULT,
    "trace_downstream": _TRACE_RESULT,
    "find_common_collectors": {
        "type": "object",
        "required": ["start", "depth", "min_sources", "collectors", "total_count", "truncated", "warnings", "sources"],
        "properties": {"collectors": {"type": "array", "items": {
            **BRIEF, "required": BRIEF["required"] + ["n_sources", "sources_reached", "direct_sum_kzt"]}},
            **COMMON}},
    "rank_candidates": {"type": "object", "required": ["filters", "candidates", "total_count", "truncated", "sources"],
                        "properties": {"candidates": {"type": "array", "items": {
                            **BRIEF, "required": BRIEF["required"] + ["rank", "evidence"]}},
                            "sources": COMMON["sources"]}},
    "get_cluster": {"type": "object", "required": ["cluster", "members_count", "roles", "top_members",
                                                   "warnings", "sources"],
                    "properties": {"cluster": {"type": "object", "required": [
                        "cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"]},
                        "top_members": {"type": "array", "items": BRIEF}, **COMMON}},
    "compare_nodes": {"type": "object", "required": ["nodes", "direct_links", "shared_payers",
                                                     "shared_receivers", "sources"],
                      "properties": {"nodes": {"type": "array", "items": BRIEF},
                                     "direct_links": {"type": "array", "items": EDGE},
                                     "sources": COMMON["sources"]}},
}

ERROR = {"type": "object", "required": ["ok", "tool", "error"],
         "properties": {"ok": {"enum": [False]}, "error": {"type": "object", "required": ["type", "code", "message"]}}}


# ---------------------------------------------------------------- проверка без зависимостей

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(value, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[t])


def validate(value, schema: dict, path: str = "$") -> list[str]:
    """Подмножество JSON Schema: type, enum, required, properties, items, additionalProperties,
    minimum/maximum. Возвращает список нарушений (пустой — всё верно)."""
    errors = []
    types = schema.get("type")
    if types is not None:
        types = types if isinstance(types, list) else [types]
        if not any(_is(value, t) for t in types):
            return [f"{path}: ожидался {'/'.join(types)}, получено {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} не из {schema['enum']}")
    if _is(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > {schema['maximum']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: нет поля {key!r}")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                errors += validate(value[key], sub, f"{path}.{key}")
        if schema.get("additionalProperties") is False:
            errors += [f"{path}: лишнее поле {k!r}" for k in value if k not in props]
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors += validate(item, schema["items"], f"{path}[{i}]")
    return errors
