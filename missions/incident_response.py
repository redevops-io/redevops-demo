"""Induced-fault operate loop (Phase 3, the "wow") — in-process on the Mission Runtime kernel.

    gather evidence → diagnose → [APPROVAL: remediate?] → remediate → verify (health restored)

Modeled fault: a memory limit set too low → OOMKilled → periodic restarts. A CloudWatch/Prometheus
alarm creates this incident mission rather than letting an agent silently mutate production.
sim=True flips the fault to healthy once the fix is applied.
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
from operators.operate.operator import build_operate_operator  # noqa: E402


def _incident_template(mission_id: str) -> ExecutionIntent:
    s_gather = IntentStep(outcome="incident_evidence", value_hint="high",
                          need="gather logs, metrics, restart history and the matching runbook for the alarm")
    s_diag = IntentStep(outcome="incident_diagnosis", inputs_from=["incident_evidence"], value_hint="high",
                        need="diagnose the root cause from the evidence")
    s_fix = IntentStep(outcome="incident_remediated", inputs_from=["incident_diagnosis"], value_hint="high",
                       need="apply the remediation (raise memory or roll back)",
                       constraints=["changes production — requires human approval"])
    s_verify = IntentStep(outcome="incident_verified", inputs_from=["incident_remediated"], value_hint="high",
                          need="verify the deployment is healthy and the restarts have stopped")
    return ExecutionIntent(mission_id=mission_id, rationale="incident-response template",
                           steps=[s_gather, s_diag, s_fix, s_verify])


templates.TEMPLATES.setdefault("incident_response", _incident_template)


class SimIncidentEnv:
    """Fault → healthy once the fix lands. Shares state across gather/diagnose/remediate/verify."""

    def __init__(self):
        self.remediated = False
        self.evidence = None
        self.diagnosis = None

    def signals(self) -> dict:
        if self.remediated:
            return {"restarts": 0, "oom": False, "memory_limit": "512Mi"}
        return {"restarts": 5, "oom": True, "memory_limit": "256Mi",
                "logs": ["OOMKilled", "Back-off restarting failed container"],
                "runbook": "raise the container memory limit or roll back"}

    def run(self, argv, cwd=None):
        if ("set" in argv and "resources" in argv) or "undo" in argv:
            self.remediated = True
        return (0, "ok", "")


def build_runtime(env: "SimIncidentEnv | None" = None, sim: bool = True) -> MissionRuntime:
    env = env if env is not None else (SimIncidentEnv() if sim else None)
    op = build_operate_operator(env=env)
    reg = CapabilityRegistry()
    reg.register(op.manifest)
    client = LocalOperatorClient({"operate": op})
    rt = MissionRuntime(reg, Executor(client), store=EventStore())
    rt.demo_simulated = isinstance(env, SimIncidentEnv)   # the auto-approve driver refuses non-simulated runtimes
    return rt


def create_incident_mission(rt: MissionRuntime, goal: str = "Restarts rising on the deployment — diagnose and remediate", tenant: str = ""):
    grants = ["obs:read", "k8s:write"]
    m = rt.create_mission(goal, policy_refs=grants, template="incident_response", tenant=tenant)
    rt.run(m.id)
    return m


def main(argv=None) -> None:  # pragma: no cover — demo driver
    import argparse
    ap = argparse.ArgumentParser()
    add_auto_approve_arg(ap)
    auto = ap.parse_args(argv).auto_approve or os.environ.get("APPROVE") == "1"   # legacy APPROVE=1 == --auto-approve
    env = SimIncidentEnv()
    rt = build_runtime(env)
    m = create_incident_mission(rt, tenant=demo_tenant(auto))
    print(f"incident {m.id}  state={m.state.value}")
    if env.diagnosis:
        print(f"🔎 diagnosis: {env.diagnosis['root_cause']}  (fix: {env.diagnosis['proposed_fix']['action']})")
    pending = rt.repo.pending_human(m.id)
    if pending:
        print(f"⛔ REMEDIATION GATE at {pending.get('node_id')}: {pending.get('prompt', 'apply fix?')}")
        if auto:
            run_auto_approve(rt, m.id)
            healthy = not env.signals().get("oom")
            print(f"→ approved · remediated · verified healthy={healthy}  final={m.state.value}")


if __name__ == "__main__":
    main()
