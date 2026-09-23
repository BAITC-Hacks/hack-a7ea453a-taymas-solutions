#!/usr/bin/env sh
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"
export DATA_DIR="${DATA_DIR:-$repo_dir/data}"
for name in nodes edges transactions; do
  if [ ! -f "$DATA_DIR/$name.parquet" ]; then
    echo "missing input: $DATA_DIR/$name.parquet" >&2
    exit 2
  fi
done
case "${SMOKE_TIMEOUT:-90}" in ''|*[!0-9]*) echo 'SMOKE_TIMEOUT must be seconds' >&2; exit 2;; esac

# Own only this test stack and its temporary outputs; leave other demos running.
export COMPOSE_PROJECT_NAME="money-graph-smoke-$$"
export PIPELINE_IMAGE="$COMPOSE_PROJECT_NAME-pipeline:local"
export UI_IMAGE="$COMPOSE_PROJECT_NAME-ui:local"
export UI_PORT="${UI_PORT:-0}"
export NVIDIA_API_KEY='' NVIDIA_MODEL=''
smoke_dir=$(mktemp -d "${TMPDIR:-/tmp}/money-graph-smoke.XXXXXX")
export OUT_DIR="$smoke_dir/out"
mkdir -p "$OUT_DIR"
compose() { "$repo_dir/scripts/docker-compose.sh" "$@"; }
cleanup() {
  result=$?
  trap - EXIT
  if [ "$result" -ne 0 ]; then compose logs --tail 40 >&2 || :; fi
  compose down --volumes --remove-orphans >/dev/null 2>&1 || :
  rm -rf "$smoke_dir"
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

compose build pipeline ui
compose run --name "$COMPOSE_PROJECT_NAME-pipeline" pipeline
pipeline_id="$COMPOSE_PROJECT_NAME-pipeline"
compose up -d copilot ui
copilot_id=$(compose ps -q copilot)
ui_id=$(compose ps -q ui)
test "$(docker inspect "$pipeline_id" --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.RW}}{{end}}{{end}}')" = false
test "$(docker inspect "$copilot_id" --format '{{range .Mounts}}{{if eq .Destination "/app/out"}}{{.RW}}{{end}}{{end}}')" = false
test "$(docker inspect "$ui_id" --format '{{range .Mounts}}{{if eq .Destination "/usr/share/nginx/html/out"}}{{.RW}}{{end}}{{end}}')" = false
for name in nodes_roles clusters top_nodes edge_table; do
  test -s "$OUT_DIR/$name.csv" || { echo "missing output: $name.csv" >&2; exit 1; }
done

address=$(compose port ui 80)
base_url="http://$address"
case "$UI_PORT" in
  0) :;;
  *) test "${address##*:}" = "$UI_PORT" || { echo 'Published UI_PORT differs' >&2; exit 1; };;
esac
wait_http() {
  deadline=$(( $(date +%s) + ${SMOKE_TIMEOUT:-90} ))
  until curl --fail --silent --connect-timeout 1 --max-time 3 "$base_url$1" > "$smoke_dir/response"; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "Timed out waiting for $base_url$1" >&2
      return 1
    fi
    sleep 1
  done
}
wait_http /healthz
wait_http /api/copilot/status
cp "$smoke_dir/response" "$OUT_DIR/smoke-status.json"
curl --fail --silent --show-error --max-time 20 \
  -H 'Content-Type: application/json' \
  --data '{"question":"Кого проверить первым и почему?","selected_gids":[],"use_nvidia":true}' \
  "$base_url/api/copilot/answer" > "$OUT_DIR/smoke-answer.json"

# Validate the actual reply using Python already installed in the image.
compose run --rm --no-deps --entrypoint python pipeline -c '
import json, os
from pathlib import Path
p = Path("/app/out")
assert os.access(p, os.W_OK), "out is not writable by the pipeline user"
status = json.loads((p / "smoke-status.json").read_text())
assert status["ready"] is True and status["nvidia_available"] is False and status["dataset_id"], status
reply = json.loads((p / "smoke-answer.json").read_text())
assert reply["status"] == "ok", reply.get("error")
assert reply["provider"] == "fallback" and reply["fallback_reason"] == "no_api_key"
assert reply["verification"] == "passed"
assert reply["candidates"] and reply["claims"] and reply["sources"]
assert all(isinstance(c["gid"], str) for c in reply["candidates"])
report = json.loads((p / "run_report.json").read_text())
assert report["reproducibility"]["identical"] is True
print("Pipeline ownership and verified Copilot fallback: OK (uid=%s gid=%s)" % (os.getuid(), os.getgid()))
'
if [ "${SMOKE_BROWSER:-0}" = 1 ]; then
  node scripts/docker-browser-smoke.mjs "$base_url" online
fi

# Browser uploads run the same pipeline and atomically update the live version.
python3 scripts/upload-smoke.py --url "$base_url" --data "$DATA_DIR" --compare-out "$OUT_DIR"

compose stop copilot
wait_http /healthz
curl --fail --silent --show-error --max-time 5 "$base_url/" > "$smoke_dir/index.html"
test -s "$smoke_dir/index.html"
for name in nodes_roles clusters top_nodes edge_table; do
  curl --fail --silent --show-error --max-time 5 "$base_url/out/$name.csv" > "$smoke_dir/download.csv"
  cmp "$OUT_DIR/$name.csv" "$smoke_dir/download.csv"
done
code=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 5 "$base_url/api/copilot/status")
case "$code" in 502|504) :;; *) echo "Expected unavailable Copilot, got HTTP $code" >&2; exit 1;; esac
if [ "${SMOKE_BROWSER:-0}" = 1 ]; then
  node scripts/docker-browser-smoke.mjs "$base_url" offline
fi
echo "Docker smoke passed: $base_url (fallback verified; UI/CSV survive stopped Copilot)"
