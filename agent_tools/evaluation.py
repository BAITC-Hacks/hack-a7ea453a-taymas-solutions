"""Evaluation-набор AML-вопросов и отчёт pass/fail (PAN-47).

Пять кейсов строятся по данным, без захардкоженных gid: выбор узлов — правило
(«первый в топе», «узел с наибольшим числом seed-плательщиков», «узел 4-го колена
с наибольшим входом»…), поэтому набор работает на любом прогоне пайплайна.

  priority          кого проверить первым и почему
  common_collector  кто собирает деньги с заданных gid
  trace             откуда пришли деньги и куда ушли (upstream / downstream)
  depth4            является ли узел 4-го колена конечным получателем
  empty             общий сборщик там, где его нет (пустой результат)

Каждый ответ проверяется верификатором и ожиданиями кейса. Ответчик подключается
функцией answer_fn(case, tools) -> answer: template_answer — локальный эталон без LLM;
оркестратор PAN-46 (fallback и NVIDIA) подключается так же:

    python -m agent_tools.evaluation --out out
    python -m agent_tools.evaluation --out out --answerer my_pkg.orchestrator:answer_case
"""

import argparse
import importlib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import GraphStore, GraphTools, call_tool
from .answer import cluster_claim, edge_claim, node_claim
from .verifier import verify


@dataclass
class EvalCase:
    id: str
    intent: str
    question: str
    tool_calls: list[dict]
    expect: dict = field(default_factory=dict)


# ---------------------------------------------------------------- кейсы из данных

def build_cases(tools: GraphTools) -> list[EvalCase]:
    n, e = tools.store.nodes, tools.store.edges
    by_prio = lambda df: df.sort_values(["priority_score", "gid"], ascending=[False, True])
    cases = []

    # 1. приоритет: первый в топ-листе
    g = int(tools.store.top.gid.iat[0])
    cases.append(EvalCase("priority", "priority", "Кого проверить первым и почему?",
                          [{"tool": "rank_candidates", "args": {"limit": 5}},
                           {"tool": "get_node", "args": {"gid": str(g)}}],
                          {"include_gids": [g], "facts": [("node", g, "priority_score"), ("node", g, "role")]}))

    # 2. общий сборщик: узел с наибольшим числом seed-плательщиков, вопрос — про этих seed
    seeds = set(n.gid[n.is_seed])
    cand = n[(n.n_seed_payers >= 2) & ~n.is_seed]
    if cand.empty:
        cand = n[n.n_payers >= 2]
    v = int(cand.sort_values(["n_seed_payers", "n_payers", "priority_score", "gid"],
                             ascending=[False, False, False, True]).gid.iat[0])
    payers = sorted(int(s) for s in e.src[e.dst == v])
    sources = ([p for p in payers if p in seeds] or payers)[:5]
    if len(sources) < 2:
        sources = payers[:5]
    cases.append(EvalCase("common_collector", "common_collector",
                          f"Кто собирает деньги с этих gid: {', '.join(map(str, sources))}?",
                          [{"tool": "find_common_collectors", "args": {"gids": [str(s) for s in sources]}}],
                          {"include_gids": [v], "facts": [("node", v, "role"), ("edge", sources[0], v, "sum_kzt")]}))

    # 3. upstream / downstream: не-seed узел с наибольшим приоритетом, у которого виден и вход, и выход
    mid = by_prio(n[~n.is_seed & (n.in_deg > 0) & (n.out_deg > 0) & n.out_observable.astype(bool)])
    x = int(mid.gid.iat[0])
    top_in = e[e.dst == x].sort_values(["sum_kzt", "src"], ascending=[False, True]).iloc[0]
    top_out = e[e.src == x].sort_values(["sum_kzt", "dst"], ascending=[False, True]).iloc[0]
    p, r = int(top_in.src), int(top_out.dst)
    cases.append(EvalCase("trace", "trace", f"Откуда пришли деньги к {x} и куда они ушли дальше?",
                          [{"tool": "trace_upstream", "args": {"gids": [str(x)], "depth": 1}},
                           {"tool": "trace_downstream", "args": {"gid": str(x), "depth": 1}}],
                          {"include_gids": [x, p, r], "facts": [("edge", p, x, "sum_kzt"), ("edge", x, r, "sum_kzt")]}))

    # 4. граница обхода: узел 4-го колена с наибольшим входом
    d4 = n[n.depth == 4].sort_values(["in_kzt", "gid"], ascending=[False, True])
    y = int(d4.gid.iat[0])
    cases.append(EvalCase("depth4", "depth4", f"Является ли {y} конечным получателем денег?",
                          [{"tool": "get_node", "args": {"gid": str(y)}},
                           {"tool": "get_outgoing", "args": {"gid": str(y)}}],
                          {"include_gids": [y], "warnings": [("depth4_outflow_unobserved", y)],
                           "not_role": {y: "terminal"}, "facts": [("node", y, "in_kzt")]}))

    # 5. пустой результат: у двух узлов нет исходящих — общего сборщика быть не может
    sinks = n[n.out_deg == 0].sort_values(["in_deg", "gid"])
    a, b = (int(z) for z in sinks.gid.iloc[:2])
    cases.append(EvalCase("empty", "common_collector", f"Кто общий сборщик денег для {a} и {b}?",
                          [{"tool": "find_common_collectors", "args": {"gids": [str(a), str(b)]}}],
                          {"include_gids": [a, b], "empty": True}))
    return cases


# ---------------------------------------------------------------- эталонный ответчик без LLM

def _kzt(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ") + " KZT"


def template_answer(case: EvalCase, tools: GraphTools) -> dict:
    """Детерминированный ответ из результатов инструментов — эталон для набора и
    нижняя планка качества: любой LLM-ответ должен проходить те же проверки."""
    res = [call_tool(tools, c["tool"], c["args"]) for c in case.tool_calls]
    results = [r["result"] for r in res if r["ok"]]
    warnings, seen = [], set()
    for r in results:
        for w in r.get("warnings", []):
            key = (w["code"], w.get("gid"))
            if key not in seen:
                seen.add(key)
                warnings.append(w)
    store = tools.store
    ans = {"question": case.question, "intent": case.intent, "provider": "template", "status": "ok",
           "summary": "", "gids": [], "claims": [], "tool_calls": case.tool_calls,
           "warnings": warnings, "next_steps": []}

    if case.id == "priority":
        node = results[1]["node"]
        g = node["gid"]
        ans["summary"] = (f"Первым проверить {g}: роль {node['role']}, priority_score {node['priority_score']}. "
                          f"Почему: {node['priority_why']}. Роль: {node['evidence']}. Это гипотеза для проверки.")
        ans["claims"] = [node_claim(g, "priority_score", node["priority_score"]), node_claim(g, "role", node["role"]),
                         node_claim(g, "in_kzt", node["in_kzt"])]
        ans["gids"] = [g]
        ans["next_steps"] = [f"запросить выписку по {g} за июль и сверить плательщиков"]

    elif case.intent == "common_collector":
        r = results[0]
        start = r["start"]
        if not r["collectors"]:
            ans["status"] = "empty"
            ans["summary"] = ("Общего сборщика для заданных gid в данных нет: ни один узел не получает деньги "
                              "от нескольких из них. Гипотеза об общем сборщике не подтверждается.")
            ans["claims"] = [node_claim(g, "out_deg", int(store.nodes.at[g, "out_deg"])) for g in start]
            ans["claims"].append({"kind": "not_observed", "gids": start, "text": "не наблюдается общий получатель"})
            ans["gids"] = start
        else:
            c = r["collectors"][0]
            direct = [s for s in c["sources_reached"] if s["direct_sum_kzt"] is not None]
            ans["summary"] = (f"Признаки общего сборщика у {c['gid']}: до него доходят деньги {c['n_sources']} "
                              f"из заданных gid, напрямую {_kzt(c['direct_sum_kzt'])} за {c['direct_n_tx']} перев. "
                              f"Роль {c['role']}, priority_score {c['priority_score']}. Гипотеза для проверки.")
            ans["claims"] = [node_claim(c["gid"], "role", c["role"]),
                             node_claim(c["gid"], "priority_score", c["priority_score"])]
            ans["claims"] += [edge_claim(s["gid"], c["gid"], "sum_kzt", s["direct_sum_kzt"]) for s in direct]
            cid = c["cluster_id"]
            ans["claims"].append(cluster_claim(cid, "n_nodes", int(store.clusters.at[cid, "n_nodes"])))
            ans["gids"] = sorted({c["gid"], *start})
            ans["next_steps"] = [f"сравнить поступления {c['gid']} от каждого из заданных gid по датам"]

    elif case.id == "trace":
        up, down = results
        x = up["start"][0]
        e = store.edges
        top_in = e[e.dst == x].sort_values(["sum_kzt", "src"], ascending=[False, True]).iloc[0]
        top_out = e[e.src == x].sort_values(["sum_kzt", "dst"], ascending=[False, True]).iloc[0]
        p, r_ = int(top_in.src), int(top_out.dst)
        ans["summary"] = (f"К {x} деньги пришли от {len(up['nodes'])} плательщ., крупнейший — {p}: "
                          f"{_kzt(top_in.sum_kzt)}. Дальше ушли {len(down['nodes'])} получ., крупнейший — {r_}: "
                          f"{_kzt(top_out.sum_kzt)}. Цепочка — признаки движения средств, гипотеза для проверки.")
        ans["claims"] = [edge_claim(p, x, "sum_kzt", float(top_in.sum_kzt)),
                         edge_claim(x, r_, "sum_kzt", float(top_out.sum_kzt)),
                         node_claim(x, "role", str(store.nodes.at[x, "role"]))]
        ans["gids"] = sorted({x, p, r_})

    elif case.id == "depth4":
        node = results[0]["node"]
        y = node["gid"]
        ans["summary"] = (f"{y} нельзя считать конечным получателем: узел на 4-м колене, его исходящие переводы "
                          f"не выгружались. Получено {_kzt(node['in_kzt'])} от {node['n_payers']} плательщ. "
                          f"Кандидат на запрос выписки — гипотеза для проверки.")
        ans["claims"] = [node_claim(y, "in_kzt", node["in_kzt"]), node_claim(y, "depth", node["depth"]),
                         node_claim(y, "role", node["role"])]
        ans["gids"] = [y]
        ans["next_steps"] = [f"запросить исходящие переводы {y} за пределами выгрузки"]
    return ans


# ---------------------------------------------------------------- проверка и отчёт

def check_expectations(answer: dict, case: EvalCase) -> list[str]:
    exp, fails = case.expect, []
    missing = [g for g in exp.get("include_gids", []) if g not in answer.get("gids", [])]
    if missing:
        fails.append(f"нет ожидаемых gid {missing}")
    for code, gid in exp.get("warnings", []):
        if not any(w.get("code") == code and w.get("gid") == gid for w in answer.get("warnings", [])):
            fails.append(f"нет предупреждения {code} для {gid}")
    for gid, role in exp.get("not_role", {}).items():
        if any(c.get("kind") == "node" and c.get("gid") == gid and c.get("field") == "role" and c.get("value") == role
               for c in answer.get("claims", [])):
            fails.append(f"{gid} назван {role}")
    if exp.get("empty") and answer.get("status") != "empty":
        fails.append("ожидался пустой результат (status=empty)")
    for fact in exp.get("facts", []):
        kind = fact[0]
        keys = {"gid": fact[1]} if kind == "node" else {"src": fact[1], "dst": fact[2]}
        fld = fact[-1]
        if not any(c.get("kind") == kind and c.get("field") == fld and all(c.get(k) == v for k, v in keys.items())
                   for c in answer.get("claims", [])):
            fails.append(f"нет ожидаемого факта {kind} {keys} {fld}")
    return fails


def run_eval(answer_fn, tools: GraphTools, cases: list[EvalCase] | None = None, provider: str | None = None) -> dict:
    cases = cases or build_cases(tools)
    rows = []
    for case in cases:
        try:
            answer = answer_fn(case, tools)
        except Exception as exc:                      # падение ответчика — это провал кейса, а не прогона
            rows.append({"id": case.id, "intent": case.intent, "question": case.question, "passed": False,
                         "verifier_errors": [], "expectation_failures": [f"ответчик упал: {type(exc).__name__}: {exc}"],
                         "gids": [], "provider": provider})
            continue
        report = verify(answer, tools)
        fails = check_expectations(answer, case)
        rows.append({"id": case.id, "intent": case.intent, "question": case.question,
                     "passed": report["ok"] and not fails, "provider": answer.get("provider", provider),
                     "verifier_errors": [f"{e['code']}: {e['message']}" for e in report["errors"]],
                     "expectation_failures": fails, "gids": report["gids"],
                     "verified_claims": sum(s.get("status") == "verified" for s in report["claims"]),
                     "answer": answer})
    return {"provider": provider or (rows[0]["provider"] if rows else None), "total": len(rows),
            "passed": sum(r["passed"] for r in rows), "cases": rows}


def render_markdown(report: dict) -> str:
    lines = [f"# Evaluation AML-вопросов: {report['passed']}/{report['total']} пройдено (провайдер {report['provider']})", "",
             "| Кейс | Вопрос | Итог | Подтверждённых фактов | Замечания |", "|---|---|---|---:|---|"]
    for r in report["cases"]:
        notes = "; ".join(r["verifier_errors"] + r["expectation_failures"]) or "—"
        lines.append(f"| {r['id']} | {r['question']} | {'PASS' if r['passed'] else 'FAIL'} | "
                     f"{r.get('verified_claims', 0)} | {notes} |")
    return "\n".join(lines) + "\n"


def _load_answerer(spec: str):
    module, _, func = spec.partition(":")
    return getattr(importlib.import_module(module), func)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Evaluation-набор AML-вопросов: pass/fail по верификатору")
    ap.add_argument("--out", default="out", help="папка с выгрузками пайплайна")
    ap.add_argument("--answerer", default=None, help="module:function(case, tools) -> answer; по умолчанию эталон")
    ap.add_argument("--report", default=None, help="куда записать отчёт (.md; рядом .json)")
    ap.add_argument("--show", action="store_true", help="напечатать ответы целиком")
    a = ap.parse_args(argv)

    tools = GraphTools(GraphStore.from_dir(a.out))
    fn = _load_answerer(a.answerer) if a.answerer else template_answer
    report = run_eval(fn, tools, provider=None if a.answerer else "template")
    print(render_markdown(report))
    if a.show:
        for r in report["cases"]:
            print(json.dumps(r.get("answer"), ensure_ascii=False, indent=2))
    if a.report:
        path = Path(a.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report), encoding="utf-8")
        path.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                             encoding="utf-8")
    sys.exit(0 if report["passed"] == report["total"] else 1)


if __name__ == "__main__":
    main()
