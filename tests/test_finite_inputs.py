"""PAN-65: reject nonfinite inputs and overflow before graph/output construction."""

import numpy as np
import pandas as pd
import pytest

from money_graph.cli import main
from money_graph.io import FILES, SCHEMA, InputSchemaError, validate_inputs
from tests._mini import mini_frames


NUMERIC_FIELDS = [(table, column) for table, schema in SCHEMA.items()
                  for column, kind in schema.items() if kind in ("int", "float")]


@pytest.mark.parametrize("table,column", NUMERIC_FIELDS)
@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_nonfinite_numeric_inputs_are_rejected(table, column, value):
    frames = dict(zip(FILES, mini_frames()))
    frame = frames[table]
    frame[column] = frame[column].astype(object)
    frame.loc[frame.index[0], column] = value
    with pytest.raises(InputSchemaError, match=rf"{table}\.{column}:"):
        validate_inputs(*(frames[name] for name in FILES))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("table,column", [("edges", "sum_kzt"), ("nodes", "gid")])
def test_nonfinite_numeric_strings_are_rejected(value, table, column):
    frames = dict(zip(FILES, mini_frames()))
    frame = frames[table]
    frame[column] = frame[column].astype("string")
    frame.loc[frame.index[0], column] = value
    with pytest.raises(InputSchemaError, match=rf"{table}\.{column}:.*NaN/Infinity"):
        validate_inputs(*(frames[name] for name in FILES))


def test_matching_infinity_in_edges_and_transactions_is_not_consistency():
    edges, nodes, tx = mini_frames()
    src, dst = int(edges.src.iloc[0]), int(edges.dst.iloc[0])
    edges.loc[edges.index[0], "sum_kzt"] = np.inf
    tx.loc[(tx.src == src) & (tx.dst == dst), "sum_kzt"] = np.inf
    with pytest.raises(InputSchemaError) as exc:
        validate_inputs(edges, nodes, tx)
    assert "edges.sum_kzt:" in str(exc.value)
    assert "transactions.sum_kzt:" in str(exc.value)


@pytest.mark.parametrize("n_transactions", [2, 3])
def test_finite_transactions_with_overflowing_pair_are_rejected(n_transactions):
    edges, nodes, tx = mini_frames()
    src, dst = int(tx.src.iloc[0]), int(tx.dst.iloc[0])
    if n_transactions == 3:
        tx = pd.concat([tx, tx.iloc[:1]], ignore_index=True)
    tx.loc[(tx.src == src) & (tx.dst == dst), "sum_kzt"] = 1e308
    pair = (edges.src == src) & (edges.dst == dst)
    edges.loc[pair, "sum_kzt"] = 1e308
    edges.loc[pair, "n_tx"] = n_transactions
    assert np.isfinite(tx.sum_kzt).all() and np.isfinite(edges.sum_kzt).all()
    with pytest.raises(InputSchemaError, match=r"transactions\.sum_kzt:.*агрегата"):
        validate_inputs(edges, nodes, tx)


def test_total_overflow_is_rejected_even_when_each_pair_matches():
    edges, nodes, tx = mini_frames()
    edges["sum_kzt"] = 1e308
    counts = tx.groupby(["src", "dst"])["sum_kzt"].transform("size")
    tx["sum_kzt"] = 1e308 / counts
    with pytest.raises(InputSchemaError) as exc:
        validate_inputs(edges, nodes, tx)
    assert "edges.sum_kzt: переполнение общей суммы" in str(exc.value)
    assert "transactions.sum_kzt: переполнение общей суммы" in str(exc.value)


@pytest.mark.parametrize("as_string", [False, True])
def test_finite_input_preserves_exact_large_int64_gids(as_string):
    frames = mini_frames()
    for frame in frames:
        for column in ("gid", "src", "dst"):
            if column in frame:
                frame[column] = frame[column] + (2**63 - 100)
                if as_string:
                    frame[column] = frame[column].astype(str)
    before = [frame.copy(deep=True) for frame in frames]
    validated = validate_inputs(*frames)
    for original, untouched, actual in zip(frames, before, validated[:3]):
        pd.testing.assert_frame_equal(original, untouched)
        for column in ("gid", "src", "dst"):
            if column in actual:
                assert actual[column].dtype == "int64"
                assert actual[column].tolist() == [int(value) for value in original[column]]
    assert np.isfinite(validated[3].stats["turnover_kzt"])


@pytest.mark.parametrize("existing_outputs", [False, True])
def test_cli_rejects_infinity_before_writing_outputs(tmp_path, capsys, existing_outputs):
    data, out = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    frames = mini_frames()
    frames[0].loc[frames[0].index[0], "sum_kzt"] = np.inf
    for name, frame in zip(FILES, frames):
        frame.to_parquet(data / f"{name}.parquet", index=False)
    if existing_outputs:
        out.mkdir()
        (out / "nodes_roles.csv").write_bytes(b"previous valid output\n")
    with pytest.raises(SystemExit) as exc:
        main(["--data", str(data), "--out", str(out)])
    assert exc.value.code == 2
    assert "edges.sum_kzt:" in capsys.readouterr().err
    if existing_outputs:
        assert sorted(p.name for p in out.iterdir()) == ["nodes_roles.csv"]
        assert (out / "nodes_roles.csv").read_bytes() == b"previous valid output\n"
    else:
        assert not out.exists()
