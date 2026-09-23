"""Детерминированные правила ролей, role_score и evidence.

Правила проверяются сверху вниз, узел получает роль первого сработавшего правила.
Код правила пишется в колонку role_rule, чтобы за минуту объяснить любой gid.

  isolated   peripheral    нет ни одного ребра (19 seed без переводов ≥5 000 KZT)
  C1         coordinator   ≥5 плательщиков и ≥10 получателей
  C2         coordinator   ≥3 плательщика, ≥10 получателей и возвратный цикл длиной ≥3
  D1         distributor   ≥10 получателей
  K1         consolidator  ≥3 плательщика или ≥2 плательщика-seed,
                           и отток не превышает 1.2 × видимого входа
  T1         transit       не seed, pass_through 0.8–1.2
  T2         transit       не seed, pass_through 0.5–1.2 и ≥80% оттока ушло за ≤2 дня
  E1         terminal      исходящие наблюдаемы (depth<4), удержано ≥80%,
                           и ≥100 тыс. KZT, или ≥2 плательщика, или ≥3 перевода
  P-trunc    peripheral    depth=4: отток не наблюдаем, признаков сбора нет
  P-ext      peripheral    не seed, отдал > 1.2 × видимого входа: источник вне выгрузки
  P          peripheral    ниже порогов всех ролей; seed с оттоком — роль не определяется

Ловушки:
  * depth=4 никогда не получает terminal (E1 требует out_observable);
  * граф собран по исходящим, входящие видны только от клиентов выборки: узел,
    отдавший больше видимого входа, не получает ни consolidator (K1), ни transit (T1/T2);
  * у seed вход занижен: transit и terminal по pass_through для seed не применяются,
    а по одному оттоку роль seed не определяется.
"""

import numpy as np
import pandas as pd

from . import config as C

ROLES = ["consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"]


def _sat(x, lo, hi):
    """Линейное насыщение в [0, 1]: lo → 0, hi → 1."""
    return np.clip((np.asarray(x, dtype=float) - lo) / (hi - lo), 0.0, 1.0)


def rule_masks(df: pd.DataFrame) -> list[tuple[str, str, pd.Series]]:
    pt = df.pass_through_reliable
    has_io = (df.in_deg > 0) & (df.out_deg > 0)
    return [
        ("isolated", "peripheral", (df.in_deg == 0) & (df.out_deg == 0)),
        ("C1", "coordinator", (df.n_payers >= C.COORD_MIN_PAYERS) & (df.n_receivers >= C.COORD_MIN_RECEIVERS)),
        ("C2", "coordinator", (df.n_payers >= C.COORD_CYCLE_MIN_PAYERS)
                              & (df.n_receivers >= C.COORD_MIN_RECEIVERS)
                              & (df.min_cycle_len >= C.COORD_CYCLE_MIN_LEN)),
        ("D1", "distributor", df.n_receivers >= C.DISTR_MIN_RECEIVERS),
        ("K1", "consolidator", ((df.n_payers >= C.CONS_MIN_PAYERS) | (df.n_seed_payers >= C.CONS_MIN_SEED_PAYERS))
                               & ~df.external_inflow_suspected),
        ("T1", "transit", has_io & pt.between(C.TRANSIT_PT_LO, C.TRANSIT_PT_HI)),
        ("T2", "transit", has_io & pt.between(C.TRANSIT_WIDE_PT_LO, C.TRANSIT_WIDE_PT_HI)
                          & (df.fast_out_share >= C.TRANSIT_WIDE_MIN_FAST_SHARE)),
        ("E1", "terminal", df.out_observable & (df.in_deg > 0)
                           & ((df.out_deg == 0) | (pt <= C.TERMINAL_MAX_PT))
                           & ((df.in_kzt >= C.TERMINAL_MIN_KZT) | (df.n_payers >= C.TERMINAL_MIN_PAYERS)
                              | (df.in_tx >= C.TERMINAL_MIN_TX))),
        ("P-trunc", "peripheral", df.truncated_by_depth),
        ("P-ext", "peripheral", df.external_inflow_suspected),
        ("P", "peripheral", pd.Series(True, index=df.index)),
    ]


def role_scores(df: pd.DataFrame) -> pd.Series:
    """Уверенность в роли, 0–1. Базовая часть — за то, что правило сработало,
    добавки — за силу сигнала относительно порога. Все формулы детерминированы."""
    pt = df.pass_through_reliable
    fast = df.fast_out_share.fillna(0.0)
    top_rcv = df.top_receiver_share.fillna(1.0)
    retention = np.where(df.out_deg == 0, 1.0, 1.0 - pt.fillna(0.0).clip(0, 1))
    closeness_to_1 = 1.0 - _sat((pt - 1.0).abs().fillna(1.0), 0.0, 0.5)
    activity = np.maximum.reduce([_sat(df.n_payers, 1, 3), _sat(df.n_receivers, 1, 10),
                                  _sat(np.maximum(df.in_kzt, df.out_kzt), 5e4, 5e5)])
    by_rule = {
        "isolated": np.full(len(df), 0.95),
        "C1": 0.6 + 0.15 * _sat(df.n_payers, 5, 20) + 0.15 * _sat(df.n_receivers, 10, 80)
              + 0.1 * (df.n_cycles > 0),
        "C2": 0.5 + 0.1 * _sat(df.n_payers, 3, 10) + 0.15 * _sat(df.n_receivers, 10, 80)
              + 0.15 * _sat(df.n_cycles, 1, 5),
        "D1": 0.5 + 0.3 * _sat(df.n_receivers, 10, 80) + 0.2 * (1.0 - top_rcv),
        "K1": 0.5 + 0.2 * _sat(df.n_payers, 3, 15) + 0.15 * _sat(df.n_seed_payers, 1, 3)
              + 0.1 * _sat(df.in_kzt, 1e5, 2e6) + 0.05 * (df.sync_payers_max >= 2)
              - 0.1 * df.truncated_by_depth,
        "T1": 0.6 + 0.2 * closeness_to_1 + 0.2 * fast,
        "T2": 0.45 + 0.1 * closeness_to_1 + 0.2 * fast,
        "E1": 0.5 + 0.2 * _sat(retention, 0.8, 1.0) + 0.15 * _sat(df.in_kzt, 1e5, 1e6)
              + 0.15 * _sat(df.in_tx, 1, 5),
        # обрезанный узел: чем больше в него пришло, тем меньше уверенность, что он «пустой»
        "P-trunc": 0.3 + 0.3 * (1.0 - _sat(df.in_kzt, 5e4, 5e5)),
        "P-ext": 0.4 + 0.3 * (1.0 - activity),
        "P": 0.5 + 0.4 * (1.0 - activity),
    }
    score = pd.Series(np.nan, index=df.index)
    for rule, s in by_rule.items():
        mask = df.role_rule == rule
        score[mask] = np.asarray(s, dtype=float)[mask.to_numpy()]
    return score.clip(0.0, 1.0).round(3)


# ---------------------------------------------------------------- evidence

def fmt_kzt(x: float) -> str:
    if x >= 1e6:
        return f"{x / 1e6:.1f} млн"
    if x >= 1e3:
        return f"{x / 1e3:.0f} тыс."
    return f"{x:.0f}"


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _fast(r) -> str:
    if pd.isna(r.fast_out_share):
        return ""
    return f"; {_pct(r.fast_out_share)} оттока ушло за ≤{C.FAST_TRANSIT_DAYS} дн."


def _cycles(r) -> str:
    return f"; циклов возврата: {r.n_cycles}" if r.n_cycles else ""


def _payers(r) -> str:
    seeds = f" ({r.n_seed_payers} seed)" if r.n_seed_payers else ""
    return f"от {r.n_payers} плательщ.{seeds}"


def evidence_for(r) -> str:
    rule = r.role_rule
    pt = r.pass_through_reliable
    seed = "seed, вход извне не виден; " if r.is_seed else ""
    if rule == "isolated":
        text = "seed без переводов ≥5 000 KZT внутри банка за июль: связей в выгрузке нет, роль не определяется"
    elif rule in ("C1", "C2"):
        why = "возврат денег через посредников" if rule == "C2" else "сбор и веерная рассылка"
        text = (f"{seed}получает {fmt_kzt(r.in_kzt)} KZT {_payers(r)}, рассылает {fmt_kzt(r.out_kzt)} "
                f"{r.n_receivers} получ.; охват вниз {r.downstream_reach} узл.{_cycles(r)} — {why}, признаки координации")
    elif rule == "D1":
        text = (f"{seed}рассылает {fmt_kzt(r.out_kzt)} KZT {r.n_receivers} получ. за {r.out_tx} перев., "
                f"крупнейшему {_pct(r.top_receiver_share)}{_fast(r)} — веерное распределение")
    elif rule == "K1":
        sync = f", до {r.sync_payers_max} плательщ. в один день" if r.sync_payers_max >= 2 else ""
        if r.truncated_by_depth:
            tail = "; отток не наблюдаем (4-е колено)"
        elif pd.notna(pt):
            tail = f"; отдаёт дальше {_pct(pt)}"
        else:
            tail = f"; отдал {fmt_kzt(r.out_kzt)}"
        text = (f"{seed}получает {fmt_kzt(r.in_kzt)} KZT {_payers(r)} за {r.in_tx} перев.{sync}{tail} "
                f"— признаки консолидации")
    elif rule in ("T1", "T2"):
        text = (f"получил {fmt_kzt(r.in_kzt)}, отдал {fmt_kzt(r.out_kzt)} KZT ({_pct(pt)}){_fast(r)} "
                f"— характерно для транзитного счёта")
    elif rule == "E1":
        kept = (f"дальше внутри банка переводов ≥{fmt_kzt(C.MIN_TX_KZT)} нет" if r.out_deg == 0
                else f"дальше внутри банка ушло {_pct(pt)}")
        text = (f"получил {fmt_kzt(r.in_kzt)} KZT {_payers(r)} за {r.in_tx} перев., {kept} "
                f"(колено {r.depth}<{C.MAX_DEPTH}, отток наблюдаем) — признаки конечного получателя")
    elif rule == "P-trunc":
        text = (f"получил {fmt_kzt(r.in_kzt)} KZT {_payers(r)}; исходящие не наблюдаемы: обход остановлен "
                f"на {C.MAX_DEPTH}-м колене — не terminal, нужен запрос выписки")
    elif rule == "P-ext":
        cover = f"видимый вход покрывает {_pct(r.in_kzt / r.out_kzt)} оттока"
        if r.n_payers >= C.CONS_MIN_PAYERS or r.n_seed_payers >= C.CONS_MIN_SEED_PAYERS:
            text = (f"получает {fmt_kzt(r.in_kzt)} KZT {_payers(r)}, но отдал {fmt_kzt(r.out_kzt)} {r.n_receivers} получ.: "
                    f"{cover} — вероятен источник вне выгрузки, сбор не засчитан как консолидация")
        else:
            text = (f"получил {fmt_kzt(r.in_kzt)}, отдал {fmt_kzt(r.out_kzt)} KZT {r.n_receivers} получ.: "
                    f"{cover} — вероятен источник вне выгрузки; ниже порогов ролей")
    elif r.is_seed and r.out_deg > 0:
        seen_in = f", видимый вход {fmt_kzt(r.in_kzt)}" if r.in_deg else ""
        text = (f"seed, вход извне не наблюдаем{seen_in}; отдал {fmt_kzt(r.out_kzt)} KZT {r.n_receivers} получ. "
                f"за {r.out_tx} перев. — по оттоку роль не определяется")
    else:
        if r.in_deg == 0:
            flow = f"{seed}отдал {fmt_kzt(r.out_kzt)} KZT {r.n_receivers} получ."
        elif r.out_deg == 0:
            flow = f"{seed}получил {fmt_kzt(r.in_kzt)} KZT {_payers(r)} за {r.in_tx} перев., дальше не отдавал"
        else:
            kept = f" ({_pct(pt)})" if pd.notna(pt) else ""
            flow = (f"{seed}получил {fmt_kzt(r.in_kzt)} KZT {_payers(r)}, "
                    f"отдал {fmt_kzt(r.out_kzt)}{kept} {r.n_receivers} получ.")
        text = f"{flow} — ниже порогов ролей (≥{C.CONS_MIN_PAYERS} плательщ., ≥{C.DISTR_MIN_RECEIVERS} получ.)"
    if len(text) > C.EVIDENCE_MAX_LEN:
        text = text[:C.EVIDENCE_MAX_LEN - 1] + "…"
    return text


def assign_roles(df: pd.DataFrame) -> pd.DataFrame:
    rules = rule_masks(df)
    df["role_rule"] = np.select([m.fillna(False).to_numpy(dtype=bool) for _, _, m in rules],
                                [code for code, _, _ in rules], default="P")
    code_to_role = {code: role for code, role, _ in rules}
    df["role"] = df.role_rule.map(code_to_role)
    df["role_score"] = role_scores(df)
    df["evidence"] = [evidence_for(r) for r in df.itertuples(index=False)]
    return df
