"""Прогон пайплайна целиком в памяти: parquet → таблицы → байты CSV.

run() ничего не пишет на диск — это делает CLI. Так повторный запуск можно
сравнить побайтно (sha256 CSV), не трогая файлы, а тесты могут гонять
пайплайн без побочных эффектов.
"""

import hashlib
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .features import build_features
from .graph import build_graph, edge_table, edge_table_for_csv
from .io import InputReport, load, validate_inputs
from .outputs import OUTPUT_FILES, nodes_roles_table, to_csv_bytes, validate_outputs
from .ranking import assign_clusters, assign_priority, top_nodes_table
from .roles import assign_roles


@dataclass
class RunResult:
    tables: dict[str, pd.DataFrame]          # имя файла → таблица
    files: dict[str, bytes]                  # имя файла → содержимое CSV
    features: pd.DataFrame                   # все признаки и роли по узлам
    input_report: InputReport
    checks: list[str]                        # пройденные проверки выгрузок
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def total_sec(self) -> float:
        return sum(self.timings.values())

    def sha256(self) -> dict[str, str]:
        return {name: hashlib.sha256(data).hexdigest() for name, data in self.files.items()}


def run(data_dir: Path) -> RunResult:
    timings: dict[str, float] = {}

    @contextmanager
    def stage(name):
        t0 = time.perf_counter()
        yield
        timings[name] = time.perf_counter() - t0

    with stage("загрузка и проверка входа"):
        edges, nodes, tx, input_report = validate_inputs(*load(Path(data_dir)))
    with stage("таблица рёбер и граф"):
        edges_tbl = edge_table(edges, nodes, tx)
        G = build_graph(edges_tbl, nodes)
    with stage("признаки узлов"):
        df = build_features(G, nodes, tx)
    with stage("роли и evidence"):
        df = assign_roles(df)
    with stage("кластеры"):
        df, clusters = assign_clusters(df, edges, nodes)
    with stage("приоритет и топ-лист"):
        df = assign_priority(df)
        top = top_nodes_table(df)
    with stage("сборка и проверка выгрузок"):
        tables = {
            "nodes_roles.csv": nodes_roles_table(df),
            "clusters.csv": clusters,
            "top_nodes.csv": top,
            "edge_table.csv": edge_table_for_csv(edges_tbl),
        }
        assert list(tables) == list(OUTPUT_FILES)
        checks = validate_outputs(tables["nodes_roles.csv"], nodes, clusters, top)
        files = {name: to_csv_bytes(t) for name, t in tables.items()}

    return RunResult(tables=tables, files=files, features=df, input_report=input_report,
                     checks=checks, timings=timings)
