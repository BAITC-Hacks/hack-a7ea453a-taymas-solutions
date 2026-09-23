"""PAN-34: smoke-проверки пайплайна — схема nodes_roles.csv, повторный запуск, время.

Мини-граф гоняется без архива данных; тесты на реальном датасете пропускаются,
если ./data не распакована.
"""

import json

import numpy as np
import pandas as pd
import pytest

from money_graph.cli import main
from money_graph.outputs import REQUIRED, OutputSchemaError, validate_outputs
from money_graph.pipeline import run
from money_graph.report import TIME_LIMIT_SEC

from tests._mini import DATA, HAS_DATA, E, mini_frames, write_mini_parquet

OUTPUTS = ("nodes_roles.csv", "clusters.csv", "top_nodes.csv", "edge_table.csv")


@pytest.fixture(scope="module")
def mini_dir(tmp_path_factory):
    return write_mini_parquet(tmp_path_factory.mktemp("mini") / "data")


@pytest.fixture(scope="module")
def mini_run(mini_dir):
    return run(mini_dir)


def _read(out_dir):
    return {name: pd.read_csv(out_dir / name) for name in OUTPUTS}


# ---------------------------------------------------------------- сквозной прогон CLI

def test_cli_end_to_end_with_repro_check(mini_dir, tmp_path):
    main(["--data", str(mini_dir), "--out", str(tmp_path), "--check-repro"])
    for name in OUTPUTS + ("run_report.md", "run_report.json"):
        assert (tmp_path / name).stat().st_size > 0, name
    report = json.loads((tmp_path / "run_report.json").read_text(encoding="utf-8"))
    assert report["reproducibility"]["identical"] is True
    assert report["outputs"]["nodes_roles.csv"]["rows"] == 6
    assert report["total_sec"] < TIME_LIMIT_SEC


def test_two_cli_runs_write_identical_files(mini_dir, tmp_path):
    main(["--data", str(mini_dir), "--out", str(tmp_path / "a")])
    main(["--data", str(mini_dir), "--out", str(tmp_path / "b")])
    for name in OUTPUTS:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name


def test_written_csv_passes_contract_after_read_back(mini_dir, tmp_path):
    """Жюри читает CSV с диска — схема должна пережить запись и чтение."""
    main(["--data", str(mini_dir), "--out", str(tmp_path)])
    t = _read(tmp_path)
    nodes = mini_frames()[1]
    validate_outputs(t["nodes_roles.csv"], nodes, t["clusters.csv"], t["top_nodes.csv"])
    nr = t["nodes_roles.csv"]
    assert list(nr.columns[:len(REQUIRED)]) == REQUIRED
    assert nr.gid.dtype == "int64" and nr.cluster_id.dtype == "int64"
    assert nr.loc[nr.gid == E, "role"].item() != "terminal"          # depth=4


def test_input_error_exits_with_code_2(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--data", str(tmp_path / "нет"), "--out", str(tmp_path / "out")])
    assert exc.value.code == 2
    assert not (tmp_path / "out").exists()                             # на ошибке ничего не пишем


# ---------------------------------------------------------------- проверки выгрузок ловят поломки

def _mutate_nr(fn):
    return lambda nr, cl, top: (fn(nr.copy()), cl, top)


@pytest.mark.parametrize("mutate, expected", [
    (_mutate_nr(lambda nr: nr.iloc[1:]), "строка на каждый узел"),
    (_mutate_nr(lambda nr: pd.concat([nr, nr.head(1)])), "gid уникальны"),
    (_mutate_nr(lambda nr: nr.assign(gid=nr.gid.replace(nr.gid.iloc[0], 999))), "набор gid"),
    (_mutate_nr(lambda nr: nr.assign(evidence=nr.evidence.where(nr.index > 0, np.nan))), "поля заполнены"),
    (_mutate_nr(lambda nr: nr.assign(role=nr.role.where(nr.index > 0, "  "))), "поля заполнены"),
    (_mutate_nr(lambda nr: nr.assign(role=nr.role.where(nr.index > 0, "boss"))), "role из словаря"),
    (_mutate_nr(lambda nr: nr.assign(role_score=nr.role_score.where(nr.index > 0, 1.5))), r"role_score в \[0, 1\]"),
    (_mutate_nr(lambda nr: nr.assign(priority_score=-nr.priority_score - 0.1)), r"priority_score в \[0, 1\]"),
    (_mutate_nr(lambda nr: nr.assign(cluster_id=nr.cluster_id.astype(str))), "типы колонок"),
    (_mutate_nr(lambda nr: nr.assign(evidence="x" * 201)), "evidence ≤ 200"),
    (_mutate_nr(lambda nr: nr.assign(evidence="высокий скор")), "evidence содержит числа"),
    (_mutate_nr(lambda nr: nr.assign(role=np.where(nr.depth == 4, "terminal", nr.role))), "не получает terminal"),
    (_mutate_nr(lambda nr: nr[["role"] + [c for c in nr.columns if c != "role"]]), "идут первыми"),
    (lambda nr, cl, top: (nr, cl.drop(columns="hypothesis"), top), r"clusters: обязательные колонки"),
    (lambda nr, cl, top: (nr, cl.assign(n_seed=0), top), "n_seed в сумме"),
    (lambda nr, cl, top: (nr, cl[cl.cluster_id != nr.cluster_id.iloc[0]], top), "cluster_id узлов описан"),
    (lambda nr, cl, top: (nr, cl, top.iloc[::-1].assign(rank=range(1, len(top) + 1))), "отсортирован"),
    (lambda nr, cl, top: (nr, cl, top.assign(role="coordinator")), "согласованы с nodes_roles"),
    (lambda nr, cl, top: (nr, cl, top.iloc[:2]), "не меньше"),
])
def test_output_contract_violations_are_caught(mini_run, mutate, expected):
    t = mini_run.tables
    nr, cl, top = mutate(t["nodes_roles.csv"], t["clusters.csv"], t["top_nodes.csv"])
    with pytest.raises(OutputSchemaError, match=expected):
        validate_outputs(nr, mini_frames()[1], cl, top)


def test_all_output_problems_reported_at_once(mini_run):
    t = mini_run.tables
    nr = t["nodes_roles.csv"].assign(role_score=2.0, evidence="без цифр")
    with pytest.raises(OutputSchemaError) as exc:
        validate_outputs(nr, mini_frames()[1], t["clusters.csv"], t["top_nodes.csv"])
    assert "role_score" in str(exc.value) and "evidence содержит числа" in str(exc.value)


# ---------------------------------------------------------------- реальный датасет

@pytest.mark.skipif(not HAS_DATA, reason="нет data/edges.parquet — распакуйте архив данных в ./data")
def test_real_dataset_smoke(tmp_path):
    main(["--data", str(DATA), "--out", str(tmp_path / "a"), "--check-repro"])
    main(["--data", str(DATA), "--out", str(tmp_path / "b")])
    for name in OUTPUTS:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name

    t = _read(tmp_path / "a")
    nr = t["nodes_roles.csv"]
    assert len(nr) == 2248 and nr.gid.is_unique
    assert not nr[REQUIRED].isna().any().any()
    assert nr.role_score.between(0, 1).all() and nr.priority_score.between(0, 1).all()
    assert (nr.evidence.str.len() <= 200).all()
    assert not ((nr.depth == 4) & (nr.role == "terminal")).any()
    assert len(t["top_nodes.csv"]) >= 20

    report = json.loads((tmp_path / "a" / "run_report.json").read_text(encoding="utf-8"))
    assert report["reproducibility"]["identical"] is True
    assert report["total_sec"] < TIME_LIMIT_SEC
    assert report["depth4_terminal"] == 0
