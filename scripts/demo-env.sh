#!/usr/bin/env bash
# Writes deploy/.env for deploy/compose.demo.yml: the control token (the caller credential of every mission endpoint and every operator /invoke)
# and the approval secret (signs the approvals operators execute on). Both are random and local to this checkout. Run once; it never overwrites.
#   ./scripts/demo-env.sh && docker compose --env-file deploy/.env -f deploy/compose.demo.yml up --build
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
env_file=deploy/.env
if [ ! -f "$env_file" ]; then
  umask 077
  printf 'AGENTIC_OS_CONTROL_TOKEN=%s\nAGENTIC_OS_APPROVAL_SECRET=%s\n' "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" > "$env_file"
  echo "wrote $env_file (mode 600, not committed)"
else
  echo "$env_file exists; left as it is"
fi
echo "Control token for the cockpit's token field:"
sed -n 's/^AGENTIC_OS_CONTROL_TOKEN=//p' "$env_file"
