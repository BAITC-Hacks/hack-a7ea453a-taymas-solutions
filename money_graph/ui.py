"""Data loading and deterministic view selection for the local graph screen."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd


EDGE_COLUMNS = ["src", "dst", "sum_kzt", "n_tx"]
ROLE_COLUMNS = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]


def parse_gid(value: str | int | None) -> int | None:
    """Parse the analyst's search input without raising on an empty value."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def load_data(data_dir: Path, out_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    """Load the graph and generated role output used by the UI.

    ``clusters.csv`` is optional for the graph screen because cluster_id is already
    present in nodes_roles.csv. The detailed cluster screen can use it later.
    """
    edge_path = data_dir / "edges.parquet"
    roles_path = out_dir / "nodes_roles.csv"
    if not edge_path.exists():
        raise FileNotFoundError(f"Не найден файл связей: {edge_path}")
    if not roles_path.exists():
        raise FileNotFoundError(
            f"Не найден {roles_path}. Сначала запустите pipeline и создайте out/nodes_roles.csv."
        )

    edges = pd.read_parquet(edge_path)
    missing_edges = set(EDGE_COLUMNS) - set(edges.columns)
    if missing_edges:
        raise ValueError(f"В edges.parquet отсутствуют колонки: {sorted(missing_edges)}")
    edges = edges[EDGE_COLUMNS].copy()
    edges["src"] = edges["src"].astype("int64")
    edges["dst"] = edges["dst"].astype("int64")

    roles = pd.read_csv(roles_path)
    missing_roles = set(ROLE_COLUMNS) - set(roles.columns)
    if missing_roles:
        raise ValueError(f"В nodes_roles.csv отсутствуют колонки: {sorted(missing_roles)}")
    roles = roles.copy()
    roles["gid"] = roles["gid"].astype("int64")
    roles["cluster_id"] = roles["cluster_id"].astype("Int64")

    # The priority explanation is produced by the ranking stage.  Older
    # outputs may not contain it in nodes_roles.csv, so enrich the node table
    # from top_nodes.csv when that file is available.
    if "priority_why" not in roles.columns:
        roles["priority_why"] = pd.NA
    top_path = out_dir / "top_nodes.csv"
    if top_path.exists():
        top = pd.read_csv(top_path)
        missing_top = {"gid", "why"} - set(top.columns)
        if missing_top:
            raise ValueError(f"В top_nodes.csv отсутствуют колонки: {sorted(missing_top)}")
        top = top[["gid", "why"]].copy()
        top["gid"] = top["gid"].astype("int64")
        top = top.rename(columns={"why": "priority_why_from_top"})
        roles = roles.merge(top, on="gid", how="left", validate="one_to_one")
        roles["priority_why"] = roles["priority_why"].fillna(roles["priority_why_from_top"])
        roles = roles.drop(columns=["priority_why_from_top"])

    nodes_path = data_dir / "nodes.parquet"
    if nodes_path.exists():
        nodes = pd.read_parquet(nodes_path, columns=["gid", "depth", "is_seed"])
        roles = roles.merge(nodes, on="gid", how="left", suffixes=("", "_nodes"))
        for column in ("depth", "is_seed"):
            nodes_column = f"{column}_nodes"
            if nodes_column in roles.columns:
                roles[column] = roles[column].fillna(roles[nodes_column])
                roles = roles.drop(columns=[nodes_column])
            elif column not in roles.columns:
                roles[column] = pd.NA
    else:
        roles["depth"] = roles.get("depth", pd.NA)
        roles["is_seed"] = roles.get("is_seed", False)

    roles["is_seed"] = roles["is_seed"].fillna(False).astype(bool)
    clusters_path = out_dir / "clusters.csv"
    clusters = pd.read_csv(clusters_path) if clusters_path.exists() else None
    return edges, roles, clusters


def select_view(
    edges: pd.DataFrame,
    roles: pd.DataFrame,
    query: str | int | None = None,
    selected_roles: Iterable[str] | None = None,
    selected_clusters: Iterable[int] | None = None,
    depth_range: tuple[int, int] | None = None,
    seeds_only: bool = False,
    mode: str = "Соседи узла",
    max_nodes: int = 120,
) -> tuple[pd.DataFrame, pd.DataFrame, int | None, bool]:
    """Return nodes and edges for the current UI view.

    Filtering is deterministic: priority desc, then gid asc. Neighborhood mode
    keeps the selected gid and its one-hop neighbors even when a neighbor is
    outside the role/cluster filter, so money direction remains understandable.
    """
    selected_gid = parse_gid(query)
    selected_roles = set(selected_roles or [])
    selected_clusters = {int(c) for c in (selected_clusters or [])}

    mask = pd.Series(True, index=roles.index)
    if selected_roles:
        mask &= roles["role"].isin(selected_roles)
    if selected_clusters:
        mask &= roles["cluster_id"].isin(selected_clusters)
    if depth_range is not None and "depth" in roles:
        low, high = depth_range
        mask &= roles["depth"].between(low, high, inclusive="both")
    if seeds_only:
        mask &= roles["is_seed"]

    candidates = roles.loc[mask].copy()
    candidates = candidates.sort_values(["priority_score", "gid"], ascending=[False, True], na_position="last")
    all_ids = set(roles["gid"].astype("int64"))

    if mode == "Соседи узла" and selected_gid in all_ids:
        neighbor_mask = (edges["src"] == selected_gid) | (edges["dst"] == selected_gid)
        neighbor_ids = set(edges.loc[neighbor_mask, ["src", "dst"]].to_numpy().ravel().tolist())
        ids = (neighbor_ids | {selected_gid}) & all_ids
        truncated = False
    else:
        ids = set(candidates.head(max_nodes)["gid"].astype("int64"))
        if selected_gid in all_ids and mode != "Сеть по фильтрам":
            ids.add(selected_gid)
        truncated = len(candidates) > max_nodes

    if not ids:
        return roles.iloc[0:0].copy(), edges.iloc[0:0].copy(), selected_gid, False

    view_nodes = roles[roles["gid"].isin(ids)].sort_values("gid").reset_index(drop=True)
    view_edges = edges[edges["src"].isin(ids) & edges["dst"].isin(ids)].copy()
    view_edges = view_edges.sort_values(["src", "dst", "sum_kzt"], ascending=[True, True, False]).reset_index(drop=True)
    return view_nodes, view_edges, selected_gid if selected_gid in ids else None, truncated
