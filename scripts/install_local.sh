#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

cat <<'EOF'

Plant Monitor installed locally.

Use it in this shell:

  source .venv/bin/activate
  plant --help

Or without activating:

  .venv/bin/plant --help

EOF

