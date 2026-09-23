"""Отчёт о прогоне: время по этапам, окружение, выгрузки с sha256, роли, проверки.

Пишется в out/run_report.json (для машин) и out/run_report.md (для README и жюри).
В хеши выгрузок отчёт не входит — в нём есть время, которое меняется от запуска к запуску.
"""

import json
import platform
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version

from .pipeline import RunResult
from .roles import ROLES

TIME_LIMIT_SEC = 300          # ТЗ: полный пересчёт не дольше 5 минут
PACKAGES = ("pandas", "numpy", "networkx", "scipy", "pyarrow")


def _pkg(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "—"


def build_report(result: RunResult, data_dir, out_dir, repro: dict | None) -> dict:
    df = result.features
    roles = {}
    for role in ROLES:
        sub = df[df.role == role]
        roles[role] = {"n": int(len(sub)), "rules": {k: int(v) for k, v in sub.role_rule.value_counts().items()}}
    s = result.input_report.stats
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "command": f"python -m money_graph --data {data_dir} --out {out_dir}",
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        **{p: _pkg(p) for p in PACKAGES}},
        "input": {"nodes": s["nodes"], "seeds": s["seeds"], "edges": s["edges"],
                  "transactions": s["transactions"], "turnover_kzt": round(s["turnover_kzt"], 2),
                  "period": [str(d) for d in s["period"]], "warnings": result.input_report.warnings},
        "timings_sec": {k: round(v, 3) for k, v in result.timings.items()},
        "total_sec": round(result.total_sec, 3),
        "time_limit_sec": TIME_LIMIT_SEC,
        "outputs": {name: {"rows": int(len(result.tables[name])), "columns": int(result.tables[name].shape[1]),
                           "sha256": sha} for name, sha in result.sha256().items()},
        "roles": roles,
        "depth4_terminal": int(((df.depth == 4) & (df.role == "terminal")).sum()),
        "checks_passed": result.checks,
        "reproducibility": repro,
    }


def render_markdown(r: dict) -> str:
    L = ["# Отчёт о прогоне", "",
         f"`{r['command']}` · {r['generated_at']}", "",
         "## Время", "",
         "| Этап | Секунд |", "|---|---:|"]
    L += [f"| {k} | {v:.2f} |" for k, v in r["timings_sec"].items()]
    ok = "укладывается" if r["total_sec"] <= r["time_limit_sec"] else "НЕ укладывается"
    L += [f"| **итого** | **{r['total_sec']:.2f}** |", "",
          f"Лимит ТЗ — {r['time_limit_sec']} с: {ok} (запас ×{r['time_limit_sec'] / max(r['total_sec'], 1e-9):.0f}).", ""]

    rep = r["reproducibility"]
    L += ["## Воспроизводимость", ""]
    if rep is None:
        L += ["Повторный прогон не запускался (флаг `--check-repro`).", ""]
    else:
        verdict = "все выгрузки побайтно совпали" if rep["identical"] else f"РАСХОЖДЕНИЕ: {', '.join(rep['differs'])}"
        L += [f"Повторный прогон в памяти ({rep['second_run_sec']:.2f} с): {verdict}.", ""]

    L += ["## Выгрузки", "", "| Файл | Строк | Колонок | sha256 |", "|---|---:|---:|---|"]
    L += [f"| `{n}` | {o['rows']} | {o['columns']} | `{o['sha256'][:16]}…` |" for n, o in r["outputs"].items()]

    inp = r["input"]
    turnover = f"{inp['turnover_kzt']:,.0f}".replace(",", " ")
    L += ["", "## Вход", "",
          f"{inp['nodes']} узлов ({inp['seeds']} seed), {inp['edges']} рёбер, {inp['transactions']} транзакций, "
          f"оборот {turnover} KZT, период {inp['period'][0]} — {inp['period'][1]}.",
          ""]
    L += [f"- {w}" for w in inp["warnings"]]

    L += ["", "## Роли", "", "| Роль | Узлов | Правила |", "|---|---:|---|"]
    L += [f"| {role} | {v['n']} | {', '.join(f'{k}={n}' for k, n in v['rules'].items())} |"
          for role, v in r["roles"].items()]
    L += ["", f"Узлов depth=4 с ролью terminal: {r['depth4_terminal']}.", ""]

    L += [f"## Проверки выгрузок ({len(r['checks_passed'])}, все пройдены)", ""]
    L += [f"- {c}" for c in r["checks_passed"]]

    env = r["environment"]
    L += ["", "## Окружение", "",
          f"Python {env['python']} · {env['platform']} · " +
          ", ".join(f"{p} {env[p]}" for p in PACKAGES), ""]
    return "\n".join(L)


def write_report(report: dict, out_dir):
    (out_dir / "run_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "run_report.md").write_text(render_markdown(report), encoding="utf-8")


def print_summary(result: RunResult, report: dict):
    df = result.features
    print("РОЛИ")
    print("-" * 64)
    for role, v in report["roles"].items():
        detail = ", ".join(f"{k}={n}" for k, n in v["rules"].items())
        print(f"  {role:<13}: {v['n']:>5}   ({detail})")
    print(f"\n  depth=4: {int((df.depth == 4).sum())} узлов, terminal среди них: {report['depth4_terminal']}")
    print("-" * 64)
    print(f"ВРЕМЯ (лимит {TIME_LIMIT_SEC} с)")
    for k, v in result.timings.items():
        print(f"  {k:<28}: {v:6.2f} с")
    print(f"  {'итого':<28}: {result.total_sec:6.2f} с")
    print("-" * 64)
    print(f"ПРОВЕРКИ ВЫГРУЗОК: {len(result.checks)} пройдено")
    rep = report["reproducibility"]
    if rep is not None:
        print("ПОВТОРНЫЙ ПРОГОН: " + ("идентичен" if rep["identical"] else f"РАСХОЖДЕНИЕ {rep['differs']}"))
    sys.stdout.flush()
