#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${REMOTE_DOCKER_HOST:-}" ]]; then
  echo "REMOTE_DOCKER_HOST is required, for example user@nas.local" >&2
  exit 2
fi

REMOTE_APP_DIR="${REMOTE_APP_DIR:-/opt/plant-monitor}"

if [[ ! -f .env ]]; then
  echo "Missing .env. Create it locally before deploying." >&2
  exit 2
fi

if [[ ! -f plants.yaml ]]; then
  echo "Missing plants.yaml. Create it locally before deploying." >&2
  exit 2
fi

ssh "${REMOTE_DOCKER_HOST}" "mkdir -p '${REMOTE_APP_DIR}' '${REMOTE_APP_DIR}/data'"

rsync -az --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude 'data/' \
  --exclude 'logs/' \
  ./ "${REMOTE_DOCKER_HOST}:${REMOTE_APP_DIR}/"

ssh "${REMOTE_DOCKER_HOST}" "cd '${REMOTE_APP_DIR}' && docker compose up -d --build"

echo "Deployed plant-monitor to ${REMOTE_DOCKER_HOST}:${REMOTE_APP_DIR}"

