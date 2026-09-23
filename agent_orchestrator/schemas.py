"""JSON Schema 2020-12 for API/UI adapters; no runtime validation dependency."""

from .contracts import MAX_QUESTION, MAX_SELECTED, MAX_TOOL_CALLS


def obj(properties, required=None):
    return {"type": "object", "properties": properties, "additionalProperties": False,
            "required": list(properties) if required is None else required}


def array(items, **limits):
    return {"type": "array", "items": items, **limits}


STRING = {"type": "string"}
NUMBER = {"type": "number"}
INT = {"type": "integer", "minimum": 0}
GID = {"type": "integer", "minimum": 0, "maximum": 2**63 - 1}
WIRE_GID = {"type": "string", "pattern": r"^[0-9]{1,19}$"}
SCORE = {"type": "number", "minimum": 0, "maximum": 1}
ROLE = {"enum": ["consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"]}
REQUEST_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", **obj({
    "question": {"type": "string", "minLength": 1, "maxLength": MAX_QUESTION},
    "selected_gids": array({"oneOf": [GID, WIRE_GID]}, maxItems=MAX_SELECTED),
    "depth": {"type": "integer", "minimum": 1, "maximum": 4},
    "use_nvidia": {"type": "boolean"},
}, ["question"])}


def answer_schema(*, browser=False):
    gid = WIRE_GID if browser else GID
    node_source = obj({"file": {"const": "nodes_roles.csv"}, "gid": gid, "column": STRING})
    edge_source = obj({"file": {"const": "edge_table.csv"}, "src": gid, "dst": gid, "column": STRING})
    source = {"oneOf": [node_source, edge_source]}
    node_claim = obj({"kind": {"const": "node"}, "gid": gid, "field": STRING,
                      "value": {"type": ["number", "boolean", "string"]}, "source": node_source})
    edge_claim = obj({"kind": {"const": "edge"}, "src": gid, "dst": gid, "field": {"enum": ["sum_kzt", "n_tx"]},
                      "value": NUMBER, "source": edge_source})
    args = obj({"gid": gid, "gids": array(gid, maxItems=MAX_SELECTED),
                "limit": {"type": "integer", "minimum": 1, "maximum": 5},
                "depth": {"type": "integer", "minimum": 1, "maximum": 4}}, [])
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **obj({
        "question": {"type": "string", "maxLength": MAX_QUESTION},
        "intent": {"enum": ["explanation", "common_collector", "next_step", "trace", "other"]},
        "provider": {"enum": ["fallback", "nvidia"]}, "status": {"enum": ["ok", "empty", "error"]},
        "summary": STRING, "gids": array(gid, uniqueItems=True),
        "claims": array({"oneOf": [node_claim, edge_claim]}),
        "candidates": array(obj({"gid": gid, "role": ROLE, "priority_score": SCORE}), maxItems=5),
        "evidence": array(obj({"claim_index": INT})), "sources": array(source),
        "tool_calls": array(obj({"tool": {"enum": ["get_node", "compare_nodes", "find_common_collectors",
                                     "rank_candidates", "get_incoming", "get_outgoing", "trace_upstream",
                                     "trace_downstream"]}, "args": args}), maxItems=MAX_TOOL_CALLS),
        "warnings": array(obj({"code": STRING, "message": STRING, "gid": gid}, ["code", "message"])),
        "next_steps": array(STRING), "fallback_reason": {"type": ["string", "null"]},
        "error": {"oneOf": [{"type": "null"}, obj({"code": STRING, "message": STRING})]},
        "verification": {"enum": ["passed", "unavailable", "not_applicable"]},
    })}


ANSWER_SCHEMA = answer_schema()
BROWSER_ANSWER_SCHEMA = answer_schema(browser=True)
