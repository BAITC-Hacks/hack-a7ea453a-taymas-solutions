"""PAN-55: предвычисленный resilience.json совпадает с analytics.resilience.run (PAN-44)."""

import json
import math

import pandas as pd
import pytest

from analytics.resilience import run
from analytics.resilience_export import OUTPUT, compute, dumps, main, write

from tests._mini import DATA, HAS_DATA, build_outputs

RUNS = 7          # мало прогонов: тесту важна точность переноса, а не статистика


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory):
    return build_outputs(tmp_path_factory.mktemp("resilience"))


@pytest.fixture(scope="module")
def inputs(out_dir):
    nodes = pd.read_csv(out_dir / "nodes_roles.csv", dtype={"gid": "int64"})
    edges = pd.read_csv(out_dir / "edge_table.csv", dtype={"src": "int64", "dst": "int64"})
    nodes["is_seed"] = nodes.is_seed.astype(bool)
    return nodes, edges


@pytest.fixture(scope="module")
def steps(inputs):
    return [1, 2, 5, len(inputs[0])]          # последний шаг — удалить всё: доли становятся неприменимыми


@pytest.fixture(scope="module")
def payload(out_dir, steps):
    return compute(out_dir, random_runs=RUNS, seed=3, steps=steps)


@pytest.fixture(scope="module")
def reference(inputs, steps):
    nodes, edges = inputs
    return run(edges[["src", "dst", "sum_kzt"]], nodes[["gid", "depth", "is_seed"]],
               nodes[["gid", "priority_score"]], steps=steps, random_runs=RUNS, seed=3)


def _same(a, b):
    if b is None or (isinstance(b, float) and math.isnan(b)):
        return a is None
    return a == pytest.approx(b, abs=1e-12)


def test_deterministic_scenarios_match_module(payload, reference):
    sc = reference["scenarios"]
    for strategy in ("priority", "degree"):
        for _, row in sc[sc.strategy == strategy].iterrows():
            got = payload["deterministic"][strategy][str(int(row.n_removed))]
            for metric, value in row.drop(["strategy", "trial", "n_removed"]).items():
                assert _same(got[metric], value), (strategy, row.n_removed, metric)


def test_controls_and_comparisons_match_module(payload, reference):
    for _, r in reference["summary"].query("strategy in ['random', 'matched_random']").iterrows():
        got = payload["controls"][r.strategy][str(int(r.n_removed))][r.metric]
        assert _same(got["mean"], r["mean"]) and _same(got["q05"], r.q05) and _same(got["q95"], r.q95)
        assert got["n_trials"] == RUNS and got["n_valid"] == r.n_valid
    for _, r in reference["comparisons"].iterrows():
        got = payload["comparisons"][str(int(r.n_removed))][r.reference][r.metric]
        assert _same(got["priority"], r.priority) and _same(got["reference_mean"], r.reference_mean)
        assert _same(got["reference_fraction_ge_priority"], r.reference_fraction_ge_priority)


def test_zero_step_is_baseline(payload):
    assert payload["parameters"]["steps"][0] == 0
    assert payload["deterministic"]["priority"]["0"] == payload["baseline"]
    assert payload["deterministic"]["degree"]["0"] == payload["baseline"]
    assert payload["baseline"]["removed_kzt"] == 0


def test_undefined_metrics_are_null_not_zero(payload, inputs):
    everything = payload["deterministic"]["priority"][str(len(inputs[0]))]
    assert everything["n_remaining"] == 0
    assert everything["weak_pair_loss"] is None and everything["seed_reach_loss"] is None
    assert json.loads(dumps(payload)) == json.loads(dumps(payload))          # allow_nan=False не падает


def test_removal_order_is_official_priority_with_string_gids(payload, inputs, out_dir):
    nodes = inputs[0]
    order = nodes.sort_values(["priority_score", "gid"], ascending=[False, True]).gid.astype(str).tolist()
    assert payload["removals"]["priority"] == order[:len(payload["removals"]["priority"])]
    top = pd.read_csv(out_dir / "top_nodes.csv", dtype={"gid": "int64"}).gid.astype(str).tolist()
    k = min(len(top), len(payload["removals"]["priority"]))
    assert payload["removals"]["priority"][:k] == top[:k]
    assert all(isinstance(g, str) and g.isdigit() for g in payload["removals"]["degree"])


def test_sources_parameters_and_timing_recorded(payload, out_dir, inputs):
    import hashlib
    for f in ("nodes_roles.csv", "edge_table.csv"):
        assert payload["sources"][f]["sha256"] == hashlib.sha256((out_dir / f).read_bytes()).hexdigest()
    assert payload["sources"]["graph"]["n_nodes"] == len(inputs[0])
    assert payload["sources"]["graph"]["n_edges"] == len(inputs[1])
    assert payload["parameters"]["random_runs"] == RUNS and payload["parameters"]["seed"] == 3
    assert payload["timing_sec"]["experiment"] >= 0 and payload["caveats"]


def test_payload_is_reproducible(out_dir, steps, payload):
    again = compute(out_dir, random_runs=RUNS, seed=3, steps=steps)
    for key in ("baseline", "deterministic", "controls", "comparisons", "removals", "parameters", "sources"):
        assert again[key] == payload[key], key


def test_write_is_atomic_and_valid_json(payload, tmp_path):
    path = write(payload, tmp_path)
    assert path.name == OUTPUT and not list(tmp_path.glob("*.tmp"))
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == "resilience/v1"


def test_cli_missing_outputs_exit_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--out", str(tmp_path)])
    assert exc.value.code == 2 and "nodes_roles.csv" in capsys.readouterr().err
    assert not (tmp_path / OUTPUT).exists()


@pytest.mark.skipif(not HAS_DATA, reason="нет data/edges.parquet — распакуйте архив данных в ./data")
def test_real_dataset_small_run(tmp_path):
    from money_graph.cli import main as run_pipeline
    run_pipeline(["--data", str(DATA), "--out", str(tmp_path)])
    p = compute(tmp_path, random_runs=2, seed=42)
    assert p["parameters"]["steps"] == [0, 1, 5, 10, 20, 50]
    assert p["sources"]["graph"]["n_nodes"] == 2248 and len(p["removals"]["priority"]) == 50
    top = pd.read_csv(tmp_path / "top_nodes.csv", dtype={"gid": "int64"}).gid.astype(str).tolist()
    assert p["removals"]["priority"][:len(top)] == top
