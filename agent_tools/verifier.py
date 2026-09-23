"""Evidence verifier (PAN-47): ответ агента не должен содержать выдуманных фактов.

verify(answer, tools) проверяет ответ по локальным выгрузкам и возвращает отчёт:

  * все gid (в полях и в тексте) есть в nodes_roles.csv;
  * каждый claim сверяется с CSV: узел — nodes_roles.csv, ребро — edge_table.csv,
    кластер — clusters.csv; ссылка source указывает ровно на этот файл, gid/ребро и колонку;
  * summary точно совпадает с представлением типизированных claims, next_steps
    берутся из фиксированного набора; поиск чисел остаётся дополнительной диагностикой;
  * узел 4-го колена не назван terminal и сопровождается предупреждением о границе обхода;
  * в ответе есть gid и хотя бы один подтверждённый источник;
  * tool_calls — только из allow-list и с корректными аргументами;
  * нет обвинительных формулировок и нет инструкций, «всплывших» из данных.

mark_unverified(answer, report) превращает неподтверждённые claims в «не наблюдается»
или «гипотеза», чтобы UI показывал только проверенное как факт.
"""

import copy
import math
import re

from . import ALLOWED_TOOLS, PARAMETERS, GraphTools, call_tool, validate
from .answer import ANSWER_SCHEMA, NEXT_STEPS, SOURCE_FILE, render_summary
from .safety import find_injection

KZT_ABS_TOL = 0.01              # копейки float
SCORE_ABS_TOL = 1e-6
ACCUSATORY = re.compile(r"преступник|преступн\w+ групп|организатор\w*|виновн\w*|вина доказан\w*|"
                        r"отмывает|наркоторгов\w*|наркодилер\w*|главар\w*", re.IGNORECASE)
HEDGES = re.compile(r"гипотез|признак|кандидат|характерно|вероятн|требует проверки|для проверки", re.IGNORECASE)
BOUNDARY_TEXT = re.compile(r"4-(м|е|го) колен|не выгружал|обход останов", re.IGNORECASE)
GID_IN_TEXT = re.compile(r"(?<!\d)\d{15,20}(?!\d)")
DATE_IN_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}")
NUMBER = re.compile(r"(?<![\w.,])[-+]?(\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,](\d+))?"
                    r"(?:\s*(млн|тыс\.?|%))?", re.IGNORECASE)


# ---------------------------------------------------------------- числа в тексте

def parse_numbers(text: str) -> list[dict]:
    """Числа из текста с учётом «3.8 млн», «110 тыс.», «15%», «3 848 436».
    gid и даты вырезаются заранее. tol — половина последнего показанного разряда."""
    text = DATE_IN_TEXT.sub(" ", GID_IN_TEXT.sub(" ", text or ""))
    out = []
    for m in NUMBER.finditer(text):
        whole, frac, unit = m.group(1), m.group(2), (m.group(3) or "").lower()
        base = float(re.sub(r"\D", "", whole) + ("." + frac if frac else ""))
        step = 10 ** -len(frac) if frac else 1.0
        scale = 1e6 if unit.startswith("млн") else 1e3 if unit.startswith("тыс") else 1.0
        out.append({"raw": m.group(0).strip(), "value": base * scale, "tol": step * scale / 2,
                    "percent": unit == "%"})
    return out


def _collect_numbers(obj, pool: set, texts: list):
    """Все числа из вложенной структуры; строки — отдельно, для разбора."""
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        if not (isinstance(obj, float) and math.isnan(obj)):
            pool.add(float(obj))
    elif isinstance(obj, str):
        texts.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, pool, texts)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, pool, texts)


def _traced(num: dict, pool: set) -> bool:
    v, tol = num["value"], num["tol"] + 1e-9
    if num["percent"]:
        return any(abs(v - p) <= tol or abs(v - p * 100) <= tol for p in pool)
    return any(abs(v - p) <= tol for p in pool)


# ---------------------------------------------------------------- сверка claims

def _same(actual, expected, field: str) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(actual) == bool(expected) and isinstance(actual, bool) == isinstance(expected, bool)
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if isinstance(expected, float) and math.isnan(expected):
            return actual is None or (isinstance(actual, float) and math.isnan(actual))
        tol = KZT_ABS_TOL if "kzt" in field else SCORE_ABS_TOL
        return abs(float(actual) - float(expected)) <= tol
    if expected is None or (isinstance(expected, float) and math.isnan(expected)):
        return actual is None
    return actual == expected


def _py(v):
    if hasattr(v, "item"):
        v = v.item()
    return None if isinstance(v, float) and math.isnan(v) else v


def _check_claim(i: int, c: dict, tools: GraphTools, err) -> dict:
    store = tools.store
    kind = c.get("kind")
    status = {"index": i, "kind": kind}
    if kind in ("hypothesis", "not_observed"):
        status["status"] = kind
        text = c.get("text", "")
        if kind == "hypothesis" and not HEDGES.search(text):
            err("unmarked_hypothesis", f"claim {i}: гипотеза без пометки «гипотеза»/«признак» в тексте", claim=i)
        return status

    field, src = c.get("field"), c.get("source") or {}
    if not src:
        err("missing_source", f"claim {i}: факт без ссылки на источник", claim=i)
    elif src.get("file") != SOURCE_FILE[kind] or src.get("column") != field:
        err("bad_source", f"claim {i}: источник {src.get('file')}:{src.get('column')} не совпадает "
                          f"с {SOURCE_FILE[kind]}:{field}", claim=i)

    if kind == "node":
        gid = c.get("gid")
        if gid not in store.nodes.index:
            status["status"] = "not_observed"
            err("unknown_gid", f"claim {i}: gid {gid} нет в nodes_roles.csv", claim=i, gid=gid)
            return status
        if src and src.get("gid") != gid:
            err("bad_source", f"claim {i}: source.gid {src.get('gid')} ≠ gid {gid}", claim=i)
        table, row_label = store.nodes, gid
    elif kind == "edge":
        s, d = c.get("src"), c.get("dst")
        hit = store.edges[(store.edges.src == s) & (store.edges.dst == d)]
        if hit.empty:
            status["status"] = "not_observed"
            err("unknown_edge", f"claim {i}: перевода {s} → {d} нет в edge_table.csv", claim=i, src=s, dst=d)
            return status
        if src and (src.get("src"), src.get("dst")) != (s, d):
            err("bad_source", f"claim {i}: source указывает на другое ребро", claim=i)
        table, row_label = hit, hit.index[0]
    else:
        cid = c.get("cluster_id")
        if cid not in store.clusters.index:
            status["status"] = "not_observed"
            err("unknown_cluster", f"claim {i}: cluster_id {cid} нет в clusters.csv", claim=i)
            return status
        if src and src.get("cluster_id") != cid:
            err("bad_source", f"claim {i}: source.cluster_id ≠ cluster_id", claim=i)
        table, row_label = store.clusters, cid

    if field not in table.columns:
        status["status"] = "not_observed"
        err("unknown_field", f"claim {i}: колонки {field!r} нет в {SOURCE_FILE[kind]}", claim=i)
        return status
    expected = _py(table.at[row_label, field])
    status["expected"] = expected
    if _same(c.get("value"), expected, field):
        status["status"] = "verified"
    else:
        status["status"] = "mismatch"
        err("value_mismatch", f"claim {i}: {SOURCE_FILE[kind]}.{field} = {expected!r}, в ответе {c.get('value')!r}",
            claim=i, expected=expected, actual=c.get("value"))
    return status


# ---------------------------------------------------------------- основной проход

def verify(answer: dict, tools: GraphTools) -> dict:
    errors: list[dict] = []

    def err(code, message, **details):
        errors.append({"code": code, "message": message, **details})

    problems = validate(answer, ANSWER_SCHEMA, path="answer")
    if problems:
        for p in problems:
            err("schema", p)
        return {"ok": False, "errors": errors, "claims": [], "gids": [], "untraced_numbers": []}

    store = tools.store
    text = "\n".join([answer["summary"], *answer["next_steps"]])

    # --- gid: в полях, в claims и в тексте
    gids = set(answer["gids"])
    for c in answer["claims"]:
        gids.update(g for g in (c.get("gid"), c.get("src"), c.get("dst")) if g is not None)
        gids.update(c.get("gids", []))
    text_gids = {int(g) for g in GID_IN_TEXT.findall(text)}
    for g in sorted(gids | text_gids):
        if g not in store.nodes.index:
            err("unknown_gid", f"gid {g} нет в nodes_roles.csv", gid=g)
    missing = sorted(text_gids - set(answer["gids"]))
    if missing:
        err("gid_not_listed", f"gid из текста не перечислены в answer.gids: {missing}", gids=missing)
    known = sorted(g for g in gids | text_gids if g in store.nodes.index)
    if not answer["gids"]:
        err("no_gids", "ответ не перечисляет использованные gid")

    # --- claims
    statuses = [_check_claim(i, c, tools, err) for i, c in enumerate(answer["claims"])]
    if not any(s.get("status") == "verified" for s in statuses):
        err("no_verified_source", "в ответе нет ни одного факта, подтверждённого ссылкой на CSV")

    # Known numbers alone cannot bind prose to a node, field or direction.
    try:
        expected_summary = render_summary(answer)
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        err("invalid_summary_claims", "claims не позволяют построить однозначный summary")
    else:
        if answer["summary"] != expected_summary:
            err("summary_mismatch", "summary не совпадает с текстом, построенным из типизированных claims")
    if any(step not in NEXT_STEPS.values() for step in answer["next_steps"]):
        err("unsupported_next_step", "next_steps должны быть рекомендациями из фиксированного набора")

    # --- tool_calls: allow-list, аргументы, повторное выполнение для пула чисел
    pool, texts = set(), []
    for k, call in enumerate(answer["tool_calls"]):
        name, args = call["tool"], call["args"]
        if name not in ALLOWED_TOOLS:
            err("tool_not_allowed", f"tool_calls[{k}]: инструмента {name!r} нет в allow-list", tool=name)
            continue
        bad = validate(args, PARAMETERS[name], path=f"tool_calls[{k}].args")
        if bad:
            err("invalid_tool_args", "; ".join(bad), tool=name)
            continue
        resp = call_tool(tools, name, args)
        if resp["ok"]:
            _collect_numbers(resp["result"], pool, texts)
        _collect_numbers(args, pool, texts)

    # --- пул чисел: claims, строки узлов и рёбер, тексты evidence / why / гипотез кластеров
    for c in answer["claims"]:
        _collect_numbers(c.get("value"), pool, texts)
    for g in known:
        row = store.nodes.loc[g]
        _collect_numbers([_py(v) for v in row.values], pool, texts)
        top = store.top[store.top.gid == g]
        _collect_numbers([int(x) for x in top["rank"]], pool, texts)
    e = store.edges
    _collect_numbers(e[e.src.isin(known) | e.dst.isin(known)].select_dtypes("number").values.ravel().tolist(), pool, texts)
    cids = {c.get("cluster_id") for c in answer["claims"] if c.get("kind") == "cluster"}
    cids |= {int(store.nodes.at[g, "cluster_id"]) for g in known}
    for cid in cids & set(store.clusters.index):
        _collect_numbers([_py(v) for v in store.clusters.loc[cid].values], pool, texts)
    for t in texts:
        pool.update(n["value"] for n in parse_numbers(t))
    untraced = [n["raw"] for n in parse_numbers(text) if not _traced(n, pool)]
    for raw in untraced:
        err("untraced_number", f"число «{raw}» из текста ответа не найдено в данных", number=raw)

    # --- граница обхода: 4-е колено не terminal и всегда с предупреждением
    warned = {w.get("gid") for w in answer["warnings"] if w.get("code") == "depth4_outflow_unobserved"}
    for g in known:
        if int(store.nodes.at[g, "depth"]) != 4:
            continue
        if any(c.get("kind") == "node" and c.get("gid") == g and c.get("field") == "role"
               and c.get("value") == "terminal" for c in answer["claims"]):
            err("depth4_terminal", f"gid {g} на 4-м колене назван terminal", gid=g)
        if g not in warned and not BOUNDARY_TEXT.search(text):
            err("missing_boundary_warning", f"gid {g} на 4-м колене: нет предупреждения, что исходящие "
                                            "не выгружались", gid=g)

    # --- формулировки и инъекции
    for m in ACCUSATORY.findall(text):
        err("accusatory_language", f"обвинительная формулировка «{m}»: выводы — только гипотезы для проверки")
    if answer["claims"] and not HEDGES.search(text):
        err("missing_hedge", "в тексте нет пометки, что вывод — гипотеза для проверки")
    for frag in find_injection(text):
        err("instruction_echo", f"в ответ попала инструкция из данных: «{frag}»")

    return {"ok": not errors, "errors": errors, "claims": statuses, "gids": known, "untraced_numbers": untraced}


def mark_unverified(answer: dict, report: dict) -> dict:
    """Копия ответа, где неподтверждённые claims не выдаются за факты:
    расхождение или отсутствие в данных → «не наблюдается», факт без источника → «гипотеза»."""
    marked = copy.deepcopy(answer)
    if not report.get("ok"):
        marked["verification"] = "unavailable"
        marked["summary"] = "Ответ не прошёл проверку; неподтверждённый текст скрыт. Любой вывод — гипотеза для проверки."
        marked["next_steps"] = []
    by_index = {s["index"]: s for s in report.get("claims", [])}
    no_source = {e["claim"] for e in report.get("errors", []) if e["code"] in ("missing_source", "bad_source")}
    for i, c in enumerate(marked["claims"]):
        st = by_index.get(i, {}).get("status")
        if st in ("mismatch", "not_observed") and c["kind"] in SOURCE_FILE:
            where = c.get("gid") or f"{c.get('src')} → {c.get('dst')}" if c["kind"] != "cluster" else c.get("cluster_id")
            marked["claims"][i] = {"kind": "not_observed", "gids": [g for g in (c.get("gid"), c.get("src"), c.get("dst")) if g],
                                   "text": f"не наблюдается в данных: {c.get('field')} для {where}"}
        elif i in no_source and c["kind"] in SOURCE_FILE:
            marked["claims"][i] = {"kind": "hypothesis", "gids": [g for g in (c.get("gid"), c.get("src"), c.get("dst")) if g],
                                   "text": f"гипотеза (нет подтверждённого источника): {c.get('field')} = {c.get('value')!r}"}
    return marked
