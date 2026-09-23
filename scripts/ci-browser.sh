#!/usr/bin/env bash
# Uses existing out/ and installed dependencies. Owns and cleans up only its PIDs.
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
python_bin="${PYTHON:-python}"
export CI_API_PORT="${CI_API_PORT:-18769}"
export CI_UI_PORT="${CI_UI_PORT:-15179}"
export COPILOT_UI_URL="http://127.0.0.1:$CI_UI_PORT"
unset NVIDIA_API_KEY NVIDIA_MODEL
mkdir -p .ci-artifacts

# Refuse to borrow another worktree's server: both ports must be free.
"$python_bin" - <<'PY'
import os
import socket
for name in ("CI_API_PORT", "CI_UI_PORT"):
    port = int(os.environ[name])
    if not 1024 <= port <= 65535:
        raise SystemExit(f"{name} must be in 1024..65535")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))
if os.environ["CI_API_PORT"] == os.environ["CI_UI_PORT"]:
    raise SystemExit("API and UI ports must differ")
PY

api_pid=""
ui_pid=""
cleanup() {
  if [[ -n "$ui_pid" ]]; then kill "$ui_pid" 2>/dev/null || true; wait "$ui_pid" 2>/dev/null || true; fi
  if [[ -n "$api_pid" ]]; then kill "$api_pid" 2>/dev/null || true; wait "$api_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"$python_bin" -m agent_orchestrator.server --out out --port "$CI_API_PORT" > .ci-artifacts/copilot.log 2>&1 &
api_pid=$!
(
  cd frontend
  exec node node_modules/vite/bin/vite.js --config ../scripts/ci-vite.config.ts
) > .ci-artifacts/vite.log 2>&1 &
ui_pid=$!

ready=false
for ((attempt=0; attempt<60; attempt++)); do
  if ! kill -0 "$api_pid" 2>/dev/null || ! kill -0 "$ui_pid" 2>/dev/null; then
    cat .ci-artifacts/copilot.log .ci-artifacts/vite.log
    exit 1
  fi
  if curl --fail --silent --max-time 1 "$COPILOT_UI_URL/api/copilot/status" > .ci-artifacts/status.json; then
    ready=true
    break
  fi
  sleep 0.5
done
if [[ "$ready" != true ]]; then
  cat .ci-artifacts/copilot.log .ci-artifacts/vite.log
  echo 'Local API/Vite readiness timed out' >&2
  exit 1
fi
"$python_bin" - <<'PY'
import json
from pathlib import Path
status = json.loads(Path(".ci-artifacts/status.json").read_text())
assert status == {"ready": True, "nvidia_available": False}, status
PY
cd frontend
node node_modules/@playwright/test/cli.js test --trace retain-on-failure --reporter=line,html
