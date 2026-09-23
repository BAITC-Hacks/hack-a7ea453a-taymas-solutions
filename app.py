"""Local AML graph review screen for PAN-38.

Run from the repository root:
    streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from money_graph.ui import load_data, select_view


ROLE_COLORS = {
    "coordinator": "#dc2626",
    "consolidator": "#c2410c",
    "distributor": "#2563eb",
    "transit": "#0891b2",
    "terminal": "#16a34a",
    "peripheral": "#94a3b8",
}
CLUSTER_COLORS = [
    "#2563eb", "#0f766e", "#c2410c", "#7c3aed", "#be123c",
    "#0369a1", "#4d7c0f", "#a16207", "#9333ea", "#475569",
]


st.set_page_config(page_title="Граф денег", page_icon="↗", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
    :root { --ink: #102a43; --muted: #52606d; --line: #d9e2ec; --accent: #0f766e; }
    .block-container { max-width: 1500px; padding-top: 2rem; padding-bottom: 3rem; }
    .eyebrow { color: var(--accent); font-size: .72rem; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
    .page-title { color: var(--ink); font-size: clamp(2rem, 4vw, 3.7rem); font-weight: 760; letter-spacing: -.045em; line-height: 1.02; margin: .35rem 0 .55rem; }
    .page-subtitle { color: var(--muted); font-size: 1rem; margin-bottom: 1.35rem; }
    [data-testid="stMetric"] { border: 1px solid var(--line); border-radius: 12px; padding: .7rem .85rem; background: #f8fafc; }
    [data-testid="stSidebar"] { border-right: 1px solid var(--line); }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def cached_load(data_dir: str, out_dir: str):
    return load_data(Path(data_dir), Path(out_dir))


@st.cache_data(show_spinner=False)
def make_figure(view_nodes: pd.DataFrame, view_edges: pd.DataFrame, color_by: str, selected_gid: int | None):
    graph = nx.DiGraph()
    graph.add_nodes_from(view_nodes["gid"].astype(int).tolist())
    for row in view_edges.itertuples(index=False):
        graph.add_edge(int(row.src), int(row.dst), sum_kzt=float(row.sum_kzt), n_tx=int(row.n_tx))

    if not graph.nodes:
        return go.Figure()
    n = len(graph.nodes)
    layout = nx.spring_layout(graph, seed=42, iterations=80, weight=None, k=1.1 / max(n**0.5, 1))
    x = {node: float(point[0]) for node, point in layout.items()}
    y = {node: float(point[1]) for node, point in layout.items()}

    edge_x, edge_y, edge_hover = [], [], []
    annotations = []
    arrow_step = max(1, len(view_edges) // 220)
    for index, row in enumerate(view_edges.itertuples(index=False)):
        src, dst = int(row.src), int(row.dst)
        if src not in x or dst not in x:
            continue
        edge_x += [x[src], x[dst], None]
        edge_y += [y[src], y[dst], None]
        edge_hover += [f"{src} → {dst}<br>{row.sum_kzt:,.0f} KZT<br>{row.n_tx} переводов"] * 3
        if index % arrow_step == 0:
            start_x = x[src] * 0.52 + x[dst] * 0.48
            start_y = y[src] * 0.52 + y[dst] * 0.48
            end_x = x[src] * 0.57 + x[dst] * 0.43
            end_y = y[src] * 0.57 + y[dst] * 0.43
            annotations.append({
                "x": end_x, "y": end_y, "xref": "x", "yref": "y",
                "ax": start_x, "ay": start_y, "axref": "x", "ayref": "y",
                "showarrow": True, "arrowhead": 2, "arrowsize": 0.8, "arrowwidth": 1,
                "arrowcolor": "rgba(71, 85, 105, .55)", "text": "",
            })

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=edge_x, y=edge_y, mode="lines", line={"color": "rgba(100, 116, 139, .32)", "width": 1},
        hoverinfo="text", text=edge_hover, name="Переводы", showlegend=False,
    ))

    node_meta = view_nodes.set_index("gid")
    if color_by == "Роль":
        groups = [(role, view_nodes[view_nodes["role"].fillna("unknown") == role], ROLE_COLORS.get(role, "#64748b"))
                  for role in sorted(view_nodes["role"].fillna("unknown").unique())]
    else:
        cluster_values = sorted(view_nodes["cluster_id"].dropna().astype(int).unique())
        groups = [(f"Кластер {cluster}", view_nodes[view_nodes["cluster_id"] == cluster], CLUSTER_COLORS[i % len(CLUSTER_COLORS)])
                  for i, cluster in enumerate(cluster_values)]

    for label, frame, color in groups:
        score = frame["priority_score"].fillna(frame["role_score"]).fillna(0).clip(0, 1)
        hover = [
            f"gid {int(row.gid)}<br>role: {row.role}<br>cluster: {row.cluster_id}<br>priority: {row.priority_score:.3f}"
            for row in frame.itertuples(index=False)
        ]
        fig.add_trace(go.Scatter(
            x=[x[int(gid)] for gid in frame["gid"]], y=[y[int(gid)] for gid in frame["gid"]],
            mode="markers", name=label, text=hover, hoverinfo="text",
            marker={"size": (10 + 18 * score).tolist(), "color": color, "line": {"color": "rgba(255,255,255,.85)", "width": 1}, "opacity": .92},
        ))

    if selected_gid is not None and selected_gid in node_meta.index:
        fig.add_trace(go.Scatter(
            x=[x[selected_gid]], y=[y[selected_gid]], mode="markers+text", name="Выбранный узел",
            text=[str(selected_gid)], textposition="top center", hoverinfo="skip", showlegend=False,
            marker={"size": 27, "color": "rgba(255,255,255,0)", "line": {"color": "#0f172a", "width": 3}},
        ))

    fig.update_layout(
        height=700, margin={"l": 0, "r": 0, "t": 15, "b": 0},
        paper_bgcolor="#ffffff", plot_bgcolor="#f8fafc",
        xaxis={"visible": False}, yaxis={"visible": False, "scaleanchor": "x", "scaleratio": 1},
        hovermode="closest", legend={"orientation": "h", "y": 1.02, "x": 0},
        annotations=annotations,
    )
    return fig


def main():
    st.markdown('<div class="eyebrow">AML / GRAPH REVIEW</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-title">Граф денег</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-subtitle">Направление переводов, роли и кластеры для первичного приоритета проверки.</div>', unsafe_allow_html=True)

    with st.sidebar:
        st.header("Настройки просмотра")
        data_dir = st.text_input("Папка data", "data")
        out_dir = st.text_input("Папка outputs", "out")

    try:
        edges, roles, clusters = cached_load(data_dir, out_dir)
    except (FileNotFoundError, ValueError, OSError) as error:
        st.error(str(error))
        st.info("Сначала запустите pipeline: python -m money_graph --data data --out out")
        st.stop()

    all_roles = sorted(roles["role"].dropna().astype(str).unique())
    all_clusters = sorted(roles["cluster_id"].dropna().astype(int).unique())
    min_depth = int(roles["depth"].dropna().min()) if roles["depth"].notna().any() else 0
    max_depth = int(roles["depth"].dropna().max()) if roles["depth"].notna().any() else 4

    with st.sidebar:
        query = st.text_input("Поиск по gid", placeholder="например, 123456")
        selected_roles = st.multiselect("Роли", all_roles, default=[])
        selected_clusters = st.multiselect("Кластеры", all_clusters, default=[], format_func=lambda value: f"Кластер {value}")
        depth_range = st.slider("Глубина обхода", min_depth, max_depth, (min_depth, max_depth))
        seeds_only = st.checkbox("Только seed-клиенты")
        mode = st.radio("Режим сети", ["Соседи узла", "Сеть по фильтрам", "Верхние приоритеты"], index=0)
        max_nodes = st.slider("Максимум узлов", 30, 500, 120, step=10)
        color_by = st.radio("Цвет узлов", ["Роль", "Кластер"], index=0)

    mode_for_selection = "Сеть по фильтрам" if mode == "Верхние приоритеты" else mode
    view_nodes, view_edges, selected_gid, truncated = select_view(
        edges, roles, query, selected_roles, selected_clusters, depth_range,
        seeds_only, mode_for_selection, max_nodes,
    )

    if mode == "Верхние приоритеты" and not query:
        view_nodes = roles.sort_values(["priority_score", "gid"], ascending=[False, True]).head(max_nodes).copy()
        ids = set(view_nodes["gid"])
        view_edges = edges[edges["src"].isin(ids) & edges["dst"].isin(ids)].copy()
        truncated = len(roles) > max_nodes

    if query and selected_gid is None:
        st.warning("Узел с таким gid не найден в текущем output.")
    if truncated:
        st.caption(f"Показана выборка из {len(view_nodes)} узлов. Увеличьте лимит или сузьте фильтры.")

    metric_cols = st.columns(4)
    metric_cols[0].metric("Узлов в обзоре", f"{len(view_nodes):,}")
    metric_cols[1].metric("Связей в обзоре", f"{len(view_edges):,}")
    metric_cols[2].metric("Узлов в графе", f"{len(roles):,}")
    metric_cols[3].metric("Кластеров", f"{roles['cluster_id'].nunique():,}")

    if view_nodes.empty:
        st.info("Нет узлов по выбранным фильтрам.")
        return

    figure = make_figure(view_nodes, view_edges, color_by, selected_gid)
    st.plotly_chart(figure, use_container_width=True, config={"displaylogo": False, "scrollZoom": True})
    st.caption("Стрелки показывают направление src → dst. Размер узла пропорционален priority_score.")

    with st.expander("Связи в текущем обзоре", expanded=False):
        edge_table = view_edges.rename(columns={"src": "Отправитель", "dst": "Получатель", "sum_kzt": "Сумма KZT", "n_tx": "Переводы"})
        st.dataframe(edge_table, use_container_width=True, hide_index=True)

    with st.expander("Узлы в текущем обзоре", expanded=False):
        node_columns = [c for c in ["gid", "role", "cluster_id", "depth", "is_seed", "priority_score"] if c in view_nodes]
        st.dataframe(view_nodes[node_columns], use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
