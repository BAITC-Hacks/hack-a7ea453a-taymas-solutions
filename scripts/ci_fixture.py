"""Public synthetic input for CI; never reads the organizers' archive.

Run from the repository root: python -m scripts.ci_fixture --data .ci-data
The destination must not exist. IDs deliberately exceed JavaScript's safe integer.
"""

import argparse
from pathlib import Path

from tests._mini import AGENT_DEPTH, AGENT_ROWS, C, D, frames_from_tx


def write_fixture(destination: Path) -> None:
    rows, depth = list(AGENT_ROWS), dict(AGENT_DEPTH)
    # Five seed payers + ten recipients give the existing browser case a
    # coordinator. Its role and scores are still computed by the real pipeline.
    for payer in (5, 6, 7):
        depth[payer] = 0
        rows.append((payer, C, "2026-07-01", 100_000))
    for recipient in range(50, 59):
        depth[recipient] = 2
        rows.append((C, recipient, "2026-07-04", 20_000))
    # Incomplete observed inflow on a non-seed, separately from the depth-4 case.
    depth[60] = 2
    rows.extend([(C, 60, "2026-07-04", 5_000), (60, D, "2026-07-05", 40_000)])
    # Duplicates count as two transactions; they must survive the pipeline.
    rows.append(rows[0])
    offset = 800_000_000_000_000_000
    edges, nodes, transactions = frames_from_tx(
        [(offset + src, offset + dst, day, amount) for src, dst, day, amount in rows],
        {offset + gid: value for gid, value in depth.items()},
    )
    destination.mkdir(parents=True, exist_ok=False)
    for name, frame in (("edges", edges), ("nodes", nodes), ("transactions", transactions)):
        frame.to_parquet(destination / f"{name}.parquet", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    args = parser.parse_args()
    try:
        write_fixture(args.data)
    except FileExistsError:
        parser.exit(2, f"Refusing to overwrite existing input: {args.data}\n")


if __name__ == "__main__":
    main()
