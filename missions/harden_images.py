"""Governed image-hardening loop (Phase 2) — in-process on the Mission Runtime kernel.

    scan → [APPROVAL: harden?] → harden (rebuild+push) → rollout restart → re-scan (confirm cleared)

Registers a `harden_images` template and drives it with the edge-sentinel operator. sim=True shares
state between the fake ECR and the fake command runner so findings clear once an image is rebuilt+pushed.
"""
from __future__ import annotations

import os
import pathlib
import sys

_ROOT = os.environ.get("AGENTIC_OS_ROOT") or str(pathlib.Path(__file__).resolve().parents[2] / "agentic-os")
for _p in (_ROOT, os.path.join(_ROOT, "apps"), str(pathlib.Path(__file__).resolve().parents[1])):
    if pathlib.Path(_p).exists() and _p not in sys.path:
        sys.path.insert(0, _p)

from agentic_os.mission import templates  # noqa: E402
from agentic_os.mission.executor import Executor  # noqa: E402
from agentic_os.mission.operator_sdk import LocalOperatorClient  # noqa: E402
from agentic_os.mission.registry import CapabilityRegistry  # noqa: E402
from agentic_os.mission.runtime import MissionRuntime  # noqa: E402
from agentic_os.mission.store import EventStore  # noqa: E402
from agentic_os.mission.types import ExecutionIntent, IntentStep  # noqa: E402

from demo_common.demo_approval import add_auto_approve_arg, demo_tenant, run_auto_approve  # noqa: E402
from operators.edge_sentinel.operator import build_edge_sentinel_operator  # noqa: E402
from operators.edge_sentinel import core  # noqa: E402


def _harden_template(mission_id: str) -> ExecutionIntent:
    s_scan = IntentStep(outcome="scan_report", value_hint="high",
                        need="scan the ECR images used by the deployment for CVEs and misconfigurations")
    s_harden = IntentStep(outcome="image_hardened", inputs_from=["scan_report"], value_hint="high",
                          need="rebuild the affected images on a patched base and push to ECR",
                          constraints=["changes production images — requires human approval"])
    s_rollout = IntentStep(outcome="rollout_restarted", inputs_from=["image_hardened"], value_hint="high",
                           need="kubectl rollout restart the affected deployments to pull the hardened images")
    s_rescan = IntentStep(outcome="rescan_verified", inputs_from=["rollout_restarted"], value_hint="high",
                          need="re-scan the images to confirm the findings are cleared")
    return ExecutionIntent(mission_id=mission_id, rationale="harden-images template",
                           steps=[s_scan, s_harden, s_rollout, s_rescan])


templates.TEMPLATES.setdefault("harden_images", _harden_template)


class SimEcrEnv:
    """Fake ECR + command runner sharing state: findings clear once an image is rebuilt+pushed."""

    def __init__(self, critical: int = 1, high: int = 2):
        self._crit, self._high = critical, high
        self.hardened = False

    # --- ECR client surface ---
    def start_image_scan(self, **_):
        return {}

    def describe_image_scan_findings(self, **_):
        if self.hardened:
            return {"imageScanFindings": {"findingSeverityCounts": {}, "findings": []}}
        return {"imageScanFindings": {
            "findingSeverityCounts": {"CRITICAL": self._crit, "HIGH": self._high},
            "findings": [{"name": "CVE-2024-DEMO", "severity": "CRITICAL",
                          "attributes": [{"key": "package_name", "value": "openssl"}]}]}}

    # --- command runner surface (docker build/push, kubectl) ---
    def run(self, argv, cwd=None):
        if "push" in argv or "build" in argv:
            self.hardened = True
        return (0, "ok", "")


def build_runtime(env: "SimEcrEnv | None" = None, sim: bool = True) -> MissionRuntime:
    env = env if env is not None else (SimEcrEnv() if sim else None)
    sentinel = build_edge_sentinel_operator(ecr=env, run=(env.run if env else None))
    reg = CapabilityRegistry()
    reg.register(sentinel.manifest)
    client = LocalOperatorClient({"edge-sentinel": sentinel})
    rt = MissionRuntime(reg, Executor(client), store=EventStore())
    rt.demo_simulated = isinstance(env, SimEcrEnv)   # the auto-approve driver refuses non-simulated runtimes
    return rt


def create_harden_mission(rt: MissionRuntime, goal: str = "Scan and harden the deployment's ECR images", tenant: str = ""):
    grants = ["ecr:read", "ecr:write", "k8s:write"]
    m = rt.create_mission(goal, policy_refs=grants, template="harden_images", tenant=tenant)
    rt.run(m.id)
    return m


def main(argv=None) -> None:  # pragma: no cover — demo driver
    import argparse
    ap = argparse.ArgumentParser()
    add_auto_approve_arg(ap)
    auto = ap.parse_args(argv).auto_approve or os.environ.get("APPROVE") == "1"   # legacy APPROVE=1 == --auto-approve
    env = SimEcrEnv()
    rt = build_runtime(env)
    m = create_harden_mission(rt, tenant=demo_tenant(auto))
    print(f"harden mission {m.id}  state={m.state.value}")
    pending = rt.repo.pending_human(m.id)
    if pending:
        print(f"🛡  edge-sentinel found CVEs → ⛔ HARDEN GATE at {pending.get('node_id')}: {pending.get('prompt','harden?')}")
        if auto:
            run_auto_approve(rt, m.id)
            cleared = core.rescan("outreach-engine", ecr=env)["cleared"]
            print(f"→ approved · rebuilt+pushed · rolled out · re-scan cleared={cleared}  final={m.state.value}")


if __name__ == "__main__":
    main()
