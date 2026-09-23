"""Роли на границе выгрузки: внешний источник денег, seed, скорость транзита, формулировки.

Каждый сценарий — узел с известным профилем в синтетическом графе; проверяется правило,
роль и то, что evidence не утверждает больше, чем видно в данных.
"""

import numpy as np
import pytest

from money_graph.features import build_features
from money_graph.graph import build_graph, edge_table
from money_graph.io import validate_inputs
from money_graph.roles import assign_roles

from tests._mini import frames_from_tx

S1, S2 = 1, 2                                   # seed
P1, P2, P3 = 11, 12, 13                         # плательщики сборщика
X, Z, W, SLOW, LATE = 21, 22, 23, 24, 25        # проверяемые узлы 2-го колена
Y, Q, TERM, ONE = 31, 32, 33, 34                # получатели
R = list(range(41, 51))                         # 10 получателей веера S1

ROWS = [
    # S1 — seed-дистрибьютор без видимого входа: 10 получателей
    *[(S1, r, "2026-07-05", 20_000) for r in R],
    # X собирает 30 тыс. от трёх плательщиков, а отдаёт 300 тыс.: вход покрывает 10% оттока
    (P1, X, "2026-07-01", 10_000), (P2, X, "2026-07-01", 10_000), (P3, X, "2026-07-01", 10_000),
    (X, Y, "2026-07-02", 300_000),
    # Z отдаёт 200% видимого входа за день — раньше это был T2
    (P1, Z, "2026-07-03", 45_000), (Z, Y, "2026-07-04", 90_000),
    # W пересылает 60% за день — законный T2
    (P2, W, "2026-07-03", 100_000), (W, Y, "2026-07-04", 60_000),
    # SLOW пересылает всё через 6 дней: T1, доля быстрого оттока честно 0
    (P3, SLOW, "2026-07-01", 50_000), (SLOW, Y, "2026-07-07", 50_000),
    # LATE сначала отправил, потом получил: скорость транзита не определена
    (LATE, Y, "2026-07-01", 20_000), (P1, LATE, "2026-07-10", 20_000),
    # S2 — seed с крупным оттоком (раньше T3) и терминал, получивший от него 150 тыс.
    (S2, Q, "2026-07-02", 200_000), (S2, TERM, "2026-07-02", 150_000),
    # ONE удержал 90%: терминал с частичным оттоком
    (S2, ONE, "2026-07-03", 100_000), (ONE, Y, "2026-07-04", 10_000),
]
DEPTH = {S1: 0, S2: 0, P1: 1, P2: 1, P3: 1, X: 2, Z: 2, W: 2, SLOW: 2, LATE: 2,
         Y: 3, Q: 1, TERM: 1, ONE: 1, **{r: 1 for r in R}}


@pytest.fixture(scope="module")
def roles():
    edges, nodes, tx, _ = validate_inputs(*frames_from_tx(ROWS, DEPTH))
    df = build_features(build_graph(edge_table(edges, nodes, tx), nodes), nodes, tx)
    return assign_roles(df).set_index("gid")


def test_collector_with_external_inflow_is_not_consolidator(roles):
    r = roles.loc[X]
    assert r.n_payers == 3 and r.external_inflow_suspected
    assert r.role == "peripheral" and r.role_rule == "P-ext"
    assert "3 плательщ." in r.evidence and "10% оттока" in r.evidence
    assert "вне выгрузки" in r.evidence and "признаки консолидации" not in r.evidence


def test_transit_upper_bound_is_external_inflow_ratio(roles):
    z = roles.loc[Z]                                   # 200% за день
    assert z.role_rule == "P-ext" and z.role != "transit"
    w = roles.loc[W]                                   # 60% за день
    assert w.role_rule == "T2" and w.role == "transit"


def test_seed_is_not_transit_by_outflow_alone(roles):
    s = roles.loc[S2]
    assert s.role == "peripheral" and s.role_rule == "P"
    assert "seed" in s.evidence and "роль не определяется" in s.evidence
    assert not ((roles.role == "transit") & roles.is_seed).any()


def test_fast_out_share_undefined_without_prior_inflow(roles):
    assert np.isnan(roles.loc[S1, "fast_out_share"])   # нет входящих вообще
    assert np.isnan(roles.loc[LATE, "fast_out_share"]) # вход пришёл после оттока
    assert roles.loc[SLOW, "fast_out_share"] == 0.0    # вход был, ушло через 6 дней — честный 0
    assert roles.loc[W, "fast_out_share"] == 1.0


def test_seed_distributor_evidence_has_no_fake_speed(roles):
    d = roles.loc[S1]
    assert d.role == "distributor"
    assert "оттока ушло" not in d.evidence


def test_terminal_wording_is_limited_to_observed_transfers(roles):
    for gid in (TERM, ONE):
        e = roles.loc[gid]
        assert e.role == "terminal", gid
        assert "внутри банка" in e.evidence and "деньги остаются" not in e.evidence
    assert "ушло 10%" in roles.loc[ONE].evidence


def test_no_rule_contradicts_visible_inflow(roles):
    """Ни consolidator, ни transit не могут отдать больше 1.2 × видимого входа."""
    bad = roles[roles.role.isin(["consolidator", "transit"]) & roles.external_inflow_suspected]
    assert bad.empty, bad.index.tolist()
