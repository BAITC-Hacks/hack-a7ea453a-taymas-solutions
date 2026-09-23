"""Read-only доступ к выгрузкам пайплайна: nodes_roles, edge_table, clusters, top_nodes.

Файлы читаются один раз; индексы строятся в памяти. На диск ничего не пишется,
внешних источников нет — единственный вход это папка out/ после
`python -m money_graph`.
"""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .errors import InvalidArgumentError, OutputsNotFoundError, UnknownClusterError, UnknownGidError

FILES = {
    "nodes": "nodes_roles.csv",
    "edges": "edge_table.csv",
    "clusters": "clusters.csv",
    "top": "top_nodes.csv",
}
RUN_REPORT = "run_report.json"


def py(value):
    """numpy/pandas → чистый JSON-тип; NaN → None."""
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if math.isnan(value) else float(value)
    return value


def parse_gid(value) -> int:
    """gid приходит от LLM целым числом или строкой цифр. float не принимается:
    18-значный gid в float64 теряет последние цифры."""
    if isinstance(value, bool):
        raise InvalidArgumentError(f"gid должен быть целым числом, получено {value!r}")
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise InvalidArgumentError(
        f"gid должен быть целым числом или строкой цифр, получено {value!r}"
        + (" (float теряет точность 18-значного gid)" if isinstance(value, float) else ""))


class GraphStore:
    def __init__(self, nodes: pd.DataFrame, edges: pd.DataFrame, clusters: pd.DataFrame,
                 top: pd.DataFrame, run_report: dict | None = None, source_dir: Path | None = None):
        self.nodes = nodes.set_index("gid", drop=False).sort_index()
        self.nodes.index.name = None        # иначе gid — и индекс, и колонка: sort_values("gid") неоднозначен
        self.edges = edges.sort_values(["src", "dst"]).reset_index(drop=True)
        self.clusters = clusters.set_index("cluster_id", drop=False).sort_index()
        self.clusters.index.name = None
        self.top = top.sort_values("rank").reset_index(drop=True)
        self.run_report = run_report
        self.source_dir = source_dir

        self.out_idx: dict[int, list[int]] = {}
        self.in_idx: dict[int, list[int]] = {}
        for i, (s, d) in enumerate(zip(self.edges.src, self.edges.dst)):
            self.out_idx.setdefault(int(s), []).append(i)
            self.in_idx.setdefault(int(d), []).append(i)

    @classmethod
    def from_dir(cls, out_dir) -> "GraphStore":
        out_dir = Path(out_dir)
        missing = [f for f in FILES.values() if not (out_dir / f).exists()]
        if missing:
            raise OutputsNotFoundError(
                f"в {out_dir} нет {', '.join(missing)}: сначала выполните "
                f"python -m money_graph --data data --out {out_dir}", missing=missing)
        read = lambda name, **kw: pd.read_csv(out_dir / FILES[name], **kw)
        nodes = read("nodes", dtype={"gid": "int64"})
        edges = read("edges", dtype={"src": "int64", "dst": "int64"})
        clusters = read("clusters")
        top = read("top", dtype={"gid": "int64"})
        report_path = out_dir / RUN_REPORT
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
        return cls(nodes, edges, clusters, top, report, out_dir)

    # ---------------------------------------------------------------- доступ

    def node(self, gid) -> pd.Series:
        gid = parse_gid(gid)
        if gid not in self.nodes.index:
            raise UnknownGidError(gid)
        return self.nodes.loc[gid]

    def has(self, gid: int) -> bool:
        return gid in self.nodes.index

    def cluster(self, cluster_id) -> pd.Series:
        if isinstance(cluster_id, bool) or not isinstance(cluster_id, (int, np.integer, str)) \
                or (isinstance(cluster_id, str) and not cluster_id.strip().isdigit()):
            raise InvalidArgumentError(f"cluster_id должен быть целым числом, получено {cluster_id!r}")
        cid = int(cluster_id)
        if cid not in self.clusters.index:
            raise UnknownClusterError(cid)
        return self.clusters.loc[cid]

    def out_edges(self, gid: int) -> pd.DataFrame:
        return self.edges.iloc[self.out_idx.get(gid, [])]

    def in_edges(self, gid: int) -> pd.DataFrame:
        return self.edges.iloc[self.in_idx.get(gid, [])]

    def successors(self, gid: int) -> list[int]:
        return [int(self.edges.dst.iat[i]) for i in self.out_idx.get(gid, [])]

    def predecessors(self, gid: int) -> list[int]:
        return [int(self.edges.src.iat[i]) for i in self.in_idx.get(gid, [])]
