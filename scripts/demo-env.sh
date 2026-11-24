#!/usr/bin/env bash
# Writes deploy/.env for deploy/compose.demo.yml: the control token (the caller credential of every mission endpoint and every operator /invoke)
# the approval secret (signs the approvals operators execute on), both random and local to this checkout, and AGENTIC_OS_PROFILE=development
# (this stack is a simulation: authority envelopes are not enforced). Run once; it never overwrites a secret.
#   ./scripts/demo-env.sh && docker compose --env-file deploy/.env -f deploy/compose.demo.yml up --build
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
env_file=deploy/.env
if [ ! -f "$env_file" ]; then
  umask 077
  printf 'AGENTIC_OS_CONTROL_TOKEN=%s\nAGENTIC_OS_APPROVAL_SECRET=%s\nAGENTIC_OS_PROFILE=development\n' "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" > "$env_file"
  echo "wrote $env_file (mode 600, not committed)"
else
  # written before the profile became required: add the stated choice, keep the secrets
  grep -q '^AGENTIC_OS_PROFILE=' "$env_file" || { echo 'AGENTIC_OS_PROFILE=development' >> "$env_file"; echo "added AGENTIC_OS_PROFILE=development to $env_file"; }
  echo "$env_file exists; secrets left as they are"
fi
echo "Control token for the cockpit's token field:"
sed -n 's/^AGENTIC_OS_CONTROL_TOKEN=//p' "$env_file"
