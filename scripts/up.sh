#!/usr/bin/env bash
# Bring up Projects + Sidekick locally (mirrors the redevops.io/projects one-click guide).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
docker compose up -d "$@"
echo "Projects cockpit → http://127.0.0.1:8080/cockpit"
