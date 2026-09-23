#!/usr/bin/env sh
# Preserve ownership of host bind mounts on Linux; do not chown user data.
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"
export LOCAL_UID="${LOCAL_UID:-$(id -u)}"
export LOCAL_GID="${LOCAL_GID:-$(id -g)}"
mkdir -p "${OUT_DIR:-out}"
if [ ! -w "${OUT_DIR:-out}" ]; then
  echo "Output directory is not writable by the current user: ${OUT_DIR:-out}" >&2
  exit 2
fi
exec docker compose -f "$repo_dir/docker-compose.yml" "$@"
