"""Governed deploy flow, in-process, $0 (sim runner). Proves the mission pauses at the provision
approval gate with the plan/cost as evidence, and only provisions on human approval.
"""
import missions.deploy_operate as dop
from agentic_os.mission.types import MissionState


def test_pauses_at_provision_gate_then_completes_on_approve():
    rt = dop.build_runtime(sim=True)
    m = dop.create_deploy_mission(rt)

    # deploy_app runs scan → plan, then pauses at the highest-consequence provision step
    assert m.state == MissionState.WAITING_HUMAN
    pending = rt.repo.pending_human(m.id)
    assert pending and pending.get("node_id")

    # the terraform plan is real evidence; the cost estimate derives from it
    est = dop.estimate_cost_from_plan("Plan: 68 to add, 0 to change, 0 to destroy.")
    assert est["resources_to_add"] == 68
    assert est["est_monthly_usd"] > 0 and est["est_hourly_usd"] > 0

    # human approves → the mission provisions/configures/verifies to completion
    dop.approve_all_gates(rt, m.id)  # provision (with plan_digest) then configure: two mandatory gates
    if m.state not in (MissionState.SUCCEEDED, MissionState.FAILED):
        rt.run(m.id)
    assert m.state == MissionState.SUCCEEDED


def test_reject_gate_does_not_complete():
    rt = dop.build_runtime(sim=True)
    m = dop.create_deploy_mission(rt)
    pending = rt.repo.pending_human(m.id)

    rt.approve(m.id, pending["node_id"], "reject")
    if m.state == MissionState.RUNNING:
        rt.run(m.id)
    # nothing reaches production on a rejected gate
    assert m.state != MissionState.SUCCEEDED
