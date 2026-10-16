"""Phase 2 — the governed harden loop, in-process, $0.

scan finds a seeded CRITICAL → mission pauses at the harden gate → approve → rebuild+push →
rollout restart → re-scan confirms cleared. Rejecting the gate hardens nothing.
"""
import missions.harden_images as hm
from agentic_os.mission.types import MissionState

from operators.edge_sentinel import core


def test_scan_reports_findings_then_hardens_on_approval():
    env = hm.SimEcrEnv(critical=1, high=2)
    rt = hm.build_runtime(env)
    m = hm.create_harden_mission(rt)

    # scan ran and found issues → mission pauses at the "harden?" gate
    assert m.state == MissionState.WAITING_HUMAN
    pending = rt.repo.pending_human(m.id)
    assert pending and pending.get("node_id")
    assert env.hardened is False  # nothing rebuilt before approval

    # approve → harden (rebuild+push) → rollout → re-scan
    rt.approve(m.id, pending["node_id"], "approve")
    if m.state not in (MissionState.SUCCEEDED, MissionState.FAILED):
        rt.run(m.id)
    # the rollout restart is its own approval-gated step
    assert m.state == MissionState.WAITING_HUMAN
    assert env.hardened is True and env.restarted is False
    pending = rt.repo.pending_human(m.id)
    rt.approve(m.id, pending["node_id"], "approve")
    if m.state not in (MissionState.SUCCEEDED, MissionState.FAILED):
        rt.run(m.id)
    assert m.state == MissionState.SUCCEEDED
    assert env.restarted is True
    assert env.hardened is True  # image was rebuilt+pushed

    # the re-scan now reports cleared
    assert core.rescan("outreach-engine", ecr=env)["cleared"] is True


def test_reject_gate_hardens_nothing():
    env = hm.SimEcrEnv(critical=1, high=2)
    rt = hm.build_runtime(env)
    m = hm.create_harden_mission(rt)
    pending = rt.repo.pending_human(m.id)

    rt.approve(m.id, pending["node_id"], "reject")
    if m.state == MissionState.RUNNING:
        rt.run(m.id)
    assert m.state != MissionState.SUCCEEDED
    assert env.hardened is False  # rejected → no rebuild/push touched production


def test_scan_core_shapes_findings():
    env = hm.SimEcrEnv(critical=2, high=1)
    r = core.scan_ecr("outreach-engine", ecr=env)
    assert r["critical"] == 2 and r["high"] == 1 and r["needs_hardening"] is True
