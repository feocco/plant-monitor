#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-plant-monitor:local}"

docker build -t "${IMAGE_NAME}" .
printf 'Built %s\n' "${IMAGE_NAME}"

