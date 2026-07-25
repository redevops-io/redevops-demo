"""Deploy the redevops-demo stack to GCP (GKE) as a governed Mission Runtime mission — keyless.

Reuses the cloud-agnostic mission machinery from deploy_operate (build_runtime + the deploy_app
template + the infra operator, which now reads DEMO_CLOUD / DEMO_TF_VARS from the env). GCP auth is
the owner's short-lived token — the org blocks SA keys, so there is NO Vault secret for GCP, just
GOOGLE_OAUTH_ACCESS_TOKEN minted from gcloud.

    SIM=0 (default)  real terraform against infra/terraform/envs/gcp; runs scan → plan → pauses at
                     the provision approval gate with the real plan as evidence.
    APPROVE=1        approve the gate and apply (creates GKE + Artifact Registry). Long-running.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import urllib.request

PROJECT = os.environ.get("GOOGLE_PROJECT", "gen-lang-client-0690890693")
REPO = pathlib.Path(__file__).resolve().parents[1]


def _mint_token() -> str:
    return subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True).stdout.strip()


def _setup_env() -> None:
    os.environ["DEMO_CLOUD"] = "gcp"
    os.environ["DEMO_TF_VARS"] = json.dumps({"gcp_project": PROJECT})
    os.environ.setdefault("INFRA_DEPLOY_ROOT", str(REPO / "infra"))
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT)
    if not os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN"):
        os.environ["GOOGLE_OAUTH_ACCESS_TOKEN"] = _mint_token()


def gcp_preflight() -> tuple[bool, str]:
    """Keyless GCP readiness: the owner token must reach the target project's GKE API."""
    tok = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
    if not tok:
        return False, "no GOOGLE_OAUTH_ACCESS_TOKEN (run: export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token))"
    req = urllib.request.Request(
        f"https://container.googleapis.com/v1/projects/{PROJECT}/locations/-/clusters",
        headers={"Authorization": f"Bearer {tok}"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        return True, f"owner token reaches {PROJECT}"
    except Exception as e:  # noqa: BLE001
        return False, f"token cannot reach {PROJECT}: {type(e).__name__}"


def main() -> int:
    _setup_env()
    ok, msg = gcp_preflight()
    print(f"preflight (gcp): {'✓' if ok else '✗'} {msg}")
    if not ok:
        print("⛔ preflight BLOCKED — not creating the deploy mission.")
        return 1

    from missions.deploy_operate import build_runtime, create_deploy_mission, estimate_cost_from_plan

    sim = os.environ.get("SIM", "0") != "0"
    rt = build_runtime(sim=sim)
    m = create_deploy_mission(rt, goal=f"Deploy the redevops-demo stack to GCP (GKE) in {PROJECT}")
    print(f"mission {m.id}  state={m.state.value}  (sim={sim}, cloud=gcp)")

    pending = rt.repo.pending_human(m.id)
    if pending:
        # the real terraform plan already ran as evidence for this gate
        planned = rt.repo.world(m.id).get("infra_planned", {}) if hasattr(rt.repo, "world") else {}
        summary = planned.get("summary") if isinstance(planned, dict) else None
        print(f"⛔ APPROVAL GATE at node {pending.get('node_id')}: {pending.get('prompt', 'provision')}")
        if summary:
            print(f"   plan: {summary}")
        print("   cost:", json.dumps(estimate_cost_from_plan(str(planned))))
        if os.environ.get("APPROVE") == "1":
            print("   APPROVE=1 → applying (this creates real GKE + Artifact Registry) …")
            rt.approve(m.id, pending["node_id"], "approve")
            if m.state.value not in ("succeeded", "failed"):
                rt.run(m.id)
            print(f"→ approved · final state={m.state.value}")
    else:
        print(f"final state={m.state.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
