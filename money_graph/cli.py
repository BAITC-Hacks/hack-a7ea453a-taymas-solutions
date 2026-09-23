"""Точка входа: сырые parquet → признаки → роли → выгрузки и отчёт о прогоне.

Запуск:
    python -m money_graph --data data --out out
    python -m money_graph --data data --out out --check-repro   # + повторный прогон и сверка sha256

Коды выхода: 0 — OK; 2 — вход не прошёл проверку; 3 — выгрузки не прошли проверку;
4 — повторный прогон дал другие файлы.
"""

import argparse
import sys
import time
from pathlib import Path

from .io import InputSchemaError, print_report
from .outputs import OutputSchemaError, write_outputs
from .pipeline import run
from .report import build_report, print_summary, write_report


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):   # консоль Windows по умолчанию cp1251
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Граф денег: роли, кластеры, приоритеты")
    ap.add_argument("--data", default="data", help="папка с edges/nodes/transactions.parquet")
    ap.add_argument("--out", default="out", help="куда писать выгрузки")
    ap.add_argument("--check-repro", action="store_true",
                    help="прогнать пайплайн второй раз и сверить sha256 всех выгрузок")
    a = ap.parse_args(argv)
    out_dir = Path(a.out)

    try:
        result = run(Path(a.data))
    except (FileNotFoundError, InputSchemaError) as exc:
        print(f"ОШИБКА ВХОДА: {exc}", file=sys.stderr)
        sys.exit(2)
    except OutputSchemaError as exc:
        print(f"ОШИБКА ВЫГРУЗОК: {exc}", file=sys.stderr)
        sys.exit(3)
    print_report(result.input_report)
    write_outputs(result.files, out_dir)

    repro = None
    if a.check_repro:
        t0 = time.perf_counter()
        again = run(Path(a.data)).sha256()
        first = result.sha256()
        differs = [name for name in first if first[name] != again[name]]
        repro = {"identical": not differs, "differs": differs, "second_run_sec": round(time.perf_counter() - t0, 3)}

    report = build_report(result, a.data, a.out, repro)
    write_report(report, out_dir)
    print_summary(result, report)
    print(f"Выгрузки и отчёт (run_report.md) записаны в {out_dir}/")
    if repro is not None and not repro["identical"]:
        sys.exit(4)


if __name__ == "__main__":
    main()
