#!/usr/bin/env sh
set -eu

dataset_dir=${1:-data}

for required in "$dataset_dir/nodes.parquet" "$dataset_dir/edges.parquet" "$dataset_dir/transactions.parquet"; do
  if [ ! -f "$required" ]; then
    echo "missing input: $required" >&2
    exit 2
  fi
done

docker compose up --build -d
trap 'docker compose down' EXIT INT TERM

i=0
while :; do
  if curl --fail --silent "http://127.0.0.1:${UI_PORT:-8501}/healthz" >/dev/null; then
    break
  fi
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "UI healthcheck timed out" >&2
    exit 1
  fi
  sleep 1
done

curl --fail --silent "http://127.0.0.1:${UI_PORT:-8501}/" >/dev/null
python3 scripts/upload-smoke.py --url "http://127.0.0.1:${UI_PORT:-8501}" --data "$dataset_dir"
