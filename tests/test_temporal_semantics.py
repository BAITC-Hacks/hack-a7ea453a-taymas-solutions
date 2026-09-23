"""PAN-63: близость дат не устанавливает происхождение исходящих средств."""

import numpy as np
import pandas as pd
import pytest

from money_graph import config as C
from money_graph.features import build_features, temporal_features
from money_graph.graph import build_graph, edge_table
from money_graph.io import validate_inputs
from money_graph.roles import assign_roles
from tests._mini import frames_from_tx


def _roles(rows, depth):
    edges, nodes, tx, _ = validate_inputs(*frames_from_tx(rows, depth))
    features = build_features(build_graph(edge_table(edges, nodes, tx), nodes), nodes, tx)
    return assign_roles(features).set_index("gid")


@pytest.mark.parametrize("out_kzt, rule", [(1_000_000, "T1"), (600_000, "T2")])
def test_small_recent_inflow_does_not_trace_the_outgoing_amount(out_kzt, rule):
    rows = [
        (1, 2, "2026-07-01", 995_000),
        (1, 2, "2026-07-30", 5_000),
        (2, 3, "2026-07-31", out_kzt),
    ]
    r = _roles(rows, {1: 0, 2: 1, 3: 2}).loc[2]
    assert r.fast_out_share == 1.0
    assert r.median_lag_days == 1
    assert r.role_rule == rule  # формула и порядок T1/T2 сохранены
    assert "100% оттока в ≤2 дн. от видимого входа" in r.evidence
    assert "связь сумм не установлена" in r.evidence
    assert "гипотеза транзита" in r.evidence
    assert len(r.evidence) <= C.EVIDENCE_MAX_LEN


def test_distributor_keeps_temporal_caveat():
    # Один свежий вход может совпасть по датам со всем веером выходов;
    # метрика не распределяет сумму входа между получателями.
    receivers = range(10, 20)
    rows = [(1, 2, "2026-07-30", 5_000)] + [
        (2, receiver, "2026-07-31", 100_000) for receiver in receivers
    ]
    r = _roles(rows, {1: 0, 2: 1, **{gid: 2 for gid in receivers}}).loc[2]
    assert r.role_rule == "D1" and r.fast_out_share == 1.0
    assert "от видимого входа" in r.evidence
    assert "связь сумм не установлена" in r.evidence
    assert len(r.evidence) <= C.EVIDENCE_MAX_LEN


@pytest.mark.parametrize("out_day, share, lag", [
    (9, np.nan, np.nan),  # вход только после оттока
    (10, 1.0, 0),        # тот же день: порядок неизвестен
    (12, 1.0, 2),        # граница окна включительно
    (13, 0.0, 3),        # вход виден, но вне окна
])
def test_date_window_does_not_infer_intraday_order(out_day, share, lag):
    tx = pd.DataFrame([
        (1, 2, "2026-07-10", 5_000),
        (2, 3, f"2026-07-{out_day:02d}", 1_000_000),
    ], columns=["src", "dst", "date", "sum_kzt"])
    tx["date"] = pd.to_datetime(tx.date)
    r = temporal_features(tx, pd.DataFrame({"gid": [2]})).iloc[0]
    assert r.fast_out_share == pytest.approx(share, nan_ok=True)
    assert r.median_lag_days == pytest.approx(lag, nan_ok=True)
    # Порядок строк не восстанавливает неизвестное время внутри дня.
    pd.testing.assert_frame_equal(
        temporal_features(tx, pd.DataFrame({"gid": [2]})),
        temporal_features(tx.iloc[::-1], pd.DataFrame({"gid": [2]})),
    )


def test_share_uses_all_observed_outflow_and_lag_only_matched_transfers():
    tx = pd.DataFrame([
        (2, 3, "2026-07-01", 900_000),  # нет предшествующего видимого входа
        (1, 2, "2026-07-10", 5_000),
        (2, 3, "2026-07-11", 100_000),
    ], columns=["src", "dst", "date", "sum_kzt"])
    tx["date"] = pd.to_datetime(tx.date)
    r = temporal_features(tx, pd.DataFrame({"gid": [2]})).iloc[0]
    assert r.fast_out_share == 0.1
    assert r.median_lag_days == 1
