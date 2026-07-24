"""Deploy-and-operate mission on the Mission Runtime kernel — in-process.

Composes the kernel's `deploy_app` template (scan → plan → [APPROVAL] → provision → configure →
verify) with the REAL infra operator (Terraform/Ansible). The provision step is the highest-
consequence capability, so the mission pauses at a human approval gate with the terraform plan +
cost estimate as evidence.

sim=True (default) injects a canned command runner so the whole governed flow runs at $0.
sim=False shells out to real terraform/ansible (needs deployer creds + INFRA_DEPLOY_ROOT).
"""
from __future__ import annotations

import os
import pathlib
import re
import sys


def _bootstrap_kernel() -> None:
    """Put the (separate) agentic-os kernel + apps on sys.path. Override via AGENTIC_OS_ROOT."""
    root = os.environ.get("AGENTIC_OS_ROOT") or str(pathlib.Path(__file__).resolve().parents[2] / "agentic-os")
    apps = os.environ.get("AGENTIC_OS_APPS", os.path.join(root, "apps"))
    for p in (root, apps):
        if pathlib.Path(p).exists() and p not in sys.path:
            sys.path.insert(0, p)


_bootstrap_kernel()

from agentic_os.mission.executor import Executor  # noqa: E402
from agentic_os.mission.operator_sdk import LocalOperatorClient, Operator, capability  # noqa: E402
from agentic_os.mission.registry import CapabilityRegistry  # noqa: E402
from agentic_os.mission.runtime import MissionRuntime  # noqa: E402
from agentic_os.mission.store import EventStore  # noqa: E402
from infra.operator import build_infra_operator  # noqa: E402

CLOUD = "aws"  # -> infra/terraform/envs/aws


def _sim_runner(argv, cwd=None):
    """Canned terraform/ansible outputs — the governed flow runs without touching AWS ($0)."""
    if "plan" in argv:
        return (0, "Plan: 68 to add, 0 to change, 0 to destroy.\n", "")
    if "apply" in argv:
        return (0, "Apply complete! Resources: 68 added, 0 changed, 0 destroyed.\n", "")
    if "output" in argv:
        return (0, '{"cluster_name":{"value":"redevops-demo-demo"}}\n', "")
    if "destroy" in argv:
        return (0, "Destroy complete! Resources: 68 destroyed.\n", "")
    return (0, "ok\n", "")  # ansible-playbook etc.


def _build_scanner() -> Operator:
    """Supply-chain scan (image_scanned). Placeholder in Phase 1c — edge-sentinel replaces it in Phase 2."""
    def _scan(i):
        return {"scanned": True, "critical": 0, "high": 0,
                "note": "placeholder scan — edge-sentinel ECR scanning wired in Phase 2"}
    return Operator("scanner", [
        capability("scan.image", _scan, provides=["image_scanned"],
                   permissions=["scan:read"], estimated_value="high"),
    ])


# --- rough monthly cost of the demo stack (EKS control plane + 2x t3.large + 1 NAT) ---
def estimate_cost_from_plan(plan_text: str) -> dict:
    m = re.search(r"Plan:\s*(\d+)\s*to add", plan_text or "")
    n = int(m.group(1)) if m else 0
    eks_cp = 0.10 * 730                 # ~$73/mo control plane
    nodes = 2 * 0.083 * 730             # 2x t3.large on-demand (spot is cheaper)
    nat = 0.045 * 730                   # 1 NAT gateway
    monthly = eks_cp + nodes + nat
    return {
        "resources_to_add": n,
        "est_monthly_usd": round(monthly, 0),
        "est_hourly_usd": round(monthly / 730, 2),
        "breakdown": {"eks_control_plane": round(eks_cp), "nodes_2x_t3large": round(nodes), "nat_gateway": round(nat)},
        "note": "on-demand estimate; spot nodes ~60% cheaper; $0 when torn down",
    }


def build_runtime(sim: bool = True) -> MissionRuntime:
    run = _sim_runner if sim else None
    http_get = (lambda url: 200) if sim else None
    infra = build_infra_operator(run=run, http_get=http_get)
    scanner = _build_scanner()
    reg = CapabilityRegistry()
    reg.register(infra.manifest)
    reg.register(scanner.manifest)
    client = LocalOperatorClient({"infra": infra, "scanner": scanner})
    return MissionRuntime(reg, Executor(client), store=EventStore())


def build_http_runtime(operator_urls: dict, transport=None) -> MissionRuntime:
    """SIM=0 path: the mission drives the operator *services* over HTTP `/invoke`.

    operator_urls maps operator name → base URL (e.g. {"infra": "http://infra:8230",
    "edge-sentinel": "http://edge-sentinel:8241"}). Manifests are built locally for planning;
    execution goes over the wire via HTTPOperatorClient. `transport` is injectable for tests.
    """
    from agentic_os.mission.operators import HTTPOperatorClient
    from operators.edge_sentinel.operator import build_edge_sentinel_operator

    infra = build_infra_operator()          # manifest only — real execution happens in the service
    sentinel = build_edge_sentinel_operator()  # provides image_scanned for the deploy_app scan step
    reg = CapabilityRegistry()
    reg.register(infra.manifest)
    reg.register(sentinel.manifest)
    client = HTTPOperatorClient(operator_urls, transport=transport)
    return MissionRuntime(reg, Executor(client), store=EventStore())


def create_deploy_mission(rt: MissionRuntime, goal: str = "Deploy the redevops-demo repo to AWS"):
    # scan:read → placeholder scanner (local build_runtime); ecr:read → edge-sentinel (HTTP/SIM=0 path).
    # Both provide `image_scanned`; granting both lets one mission definition drive either scanner.
    grants = ["infra:read", "infra:write", "scan:read", "ecr:read"]
    m = rt.create_mission(goal, policy_refs=grants, template="deploy_app")
    rt.run(m.id)
    return m


def preflight_gate(session_factory=None, check_cloud: bool = True):
    """Node-0 of the deploy mission (Sidekick's `deployment-preflight` skill).

    Returns (ready: bool, Report). A real deploy must not compile `terraform plan` until ready;
    sim runs pass check_cloud=False to skip the cloud probes.
    """
    from aws_demo.preflight import Report, check_aws, check_local

    report = Report()
    check_local(report)
    if check_cloud:
        try:
            from aws_demo.doctor import session_factory as default_sf
            check_aws(report, session_factory or default_sf())
        except Exception:  # noqa: BLE001
            pass
    return report.ready, report


def main() -> None:  # pragma: no cover — manual/demo driver
    import json
    from aws_demo.preflight import render

    sim = os.environ.get("SIM", "1") != "0"

    # node 0: preflight gate — refuse to deploy into an unready environment
    ready, report = preflight_gate(check_cloud=not sim)
    print(render(report))
    if not sim and not ready:
        print("\n⛔ preflight BLOCKED — not creating the deploy mission. Fix the items above.")
        return

    rt = build_runtime(sim=sim)
    m = create_deploy_mission(rt)
    print(f"mission {m.id}  state={m.state.value}  (sim={sim})")
    pending = rt.repo.pending_human(m.id)
    if pending:
        print(f"⛔ APPROVAL GATE at node {pending.get('node_id')}: {pending.get('prompt', 'provision')}")
        print("   cost estimate:", json.dumps(estimate_cost_from_plan("Plan: 68 to add, 0 to change, 0 to destroy.")))
        if os.environ.get("APPROVE") == "1":
            rt.approve(m.id, pending["node_id"], "approve")
            if m.state.value not in ("succeeded", "failed"):
                rt.run(m.id)
            print(f"→ approved · final state={m.state.value}")


if __name__ == "__main__":
    main()
