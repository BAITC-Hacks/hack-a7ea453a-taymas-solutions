#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
COPILOT_PORT="${COPILOT_PORT:-8765}"
CHECK_REPRO=0
NO_COPILOT=0
SKIP_PIP_INSTALL=0
SKIP_NPM_INSTALL=0

for arg in "$@"; do
  case "$arg" in
    --check-repro) CHECK_REPRO=1 ;;
    --no-copilot) NO_COPILOT=1 ;;
    --skip-pip-install) SKIP_PIP_INSTALL=1 ;;
    --skip-npm-install) SKIP_NPM_INSTALL=1 ;;
    --help|-h)
      cat <<'USAGE'
Usage: scripts/run-all.sh [--check-repro] [--no-copilot] [--skip-pip-install] [--skip-npm-install]

Runs the Python pipeline, starts the local Copilot API, then opens the Vite UI.
Environment:
  FRONTEND_PORT=5173
  COPILOT_PORT=8765
USAGE
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

USE_VENV=0
if [[ -x "$ROOT/.venv/bin/python" ]] && "$ROOT/.venv/bin/python" -c "import sys" >/dev/null 2>&1; then
  PYTHON="$ROOT/.venv/bin/python"
  USE_VENV=1
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  echo "Python 3.11+ not found. Install Python and run: pip install -r requirements.txt" >&2
  exit 2
fi

if [[ "$USE_VENV" != "1" ]]; then
  echo "==> Creating local Python environment"
  "$PYTHON" -m venv "$ROOT/.venv"
  PYTHON="$ROOT/.venv/bin/python"
fi

for name in nodes.parquet edges.parquet transactions.parquet; do
  if [[ ! -f "$ROOT/data/$name" ]]; then
    echo "Missing $ROOT/data/$name. Unpack the organizers' dataset into data/." >&2
    exit 2
  fi
done

mkdir -p "$ROOT/out"
cd "$ROOT"

if [[ "$SKIP_PIP_INSTALL" != "1" ]]; then
  if ! "$PYTHON" -c "import pandas, pyarrow, networkx, numpy, scipy" >/dev/null 2>&1; then
    echo "==> Installing Python dependencies"
    "$PYTHON" -m pip install -r requirements.txt
  fi
fi

pipeline_args=(-m money_graph --data data --out out)
if [[ "$CHECK_REPRO" == "1" ]]; then
  pipeline_args+=(--check-repro)
fi

echo "==> Running pipeline"
"$PYTHON" "${pipeline_args[@]}"

copilot_pid=""
cleanup() {
  if [[ -n "$copilot_pid" ]] && kill -0 "$copilot_pid" >/dev/null 2>&1; then
    kill "$copilot_pid" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

if [[ "$NO_COPILOT" != "1" ]]; then
  echo "==> Starting Copilot API on http://127.0.0.1:$COPILOT_PORT"
  "$PYTHON" -m agent_orchestrator.server --out out --port "$COPILOT_PORT" &
  copilot_pid="$!"
  sleep 2
  if ! kill -0 "$copilot_pid" >/dev/null 2>&1; then
    echo "Copilot API did not start. Check out/*.csv and whether port $COPILOT_PORT is free." >&2
    exit 2
  fi
fi

cd "$ROOT/frontend"
if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found. Install Node.js 20.19+ or 22.12+." >&2
  exit 2
fi

if [[ "$SKIP_NPM_INSTALL" != "1" && ! -d node_modules ]]; then
  echo "==> Installing frontend dependencies"
  npm ci
fi

echo "==> Starting UI on http://127.0.0.1:$FRONTEND_PORT"
echo "Press Ctrl+C to stop."
npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT"
