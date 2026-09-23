#!/usr/bin/env sh
set -eu

if [ ! -d data ]; then
  echo "data/ is missing. Put nodes.parquet, edges.parquet and transactions.parquet there." >&2
  exit 2
fi

for required in data/nodes.parquet data/edges.parquet data/transactions.parquet; do
  if [ ! -f "$required" ]; then
    echo "missing input: $required" >&2
    exit 2
  fi
done

mkdir -p out
docker compose build pipeline ui
docker compose run --rm pipeline

for required in nodes_roles.csv clusters.csv top_nodes.csv edge_table.csv; do
  if [ ! -s "out/$required" ]; then
    echo "missing or empty output: out/$required" >&2
    exit 1
  fi
done

echo "pipeline smoke check passed"
docker compose up -d ui
trap 'docker compose down' EXIT INT TERM

i=0
while :; do
  if curl --fail --silent http://127.0.0.1:8501/healthz >/dev/null; then
    break
  fi
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "UI healthcheck timed out" >&2
    exit 1
  fi
  sleep 1
done

curl --fail --silent http://127.0.0.1:8501/ >/dev/null
curl --fail --silent http://127.0.0.1:8501/out/edge_table.csv >/dev/null
echo "UI smoke check passed: http://127.0.0.1:8501"
