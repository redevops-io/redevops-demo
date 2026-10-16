"""Write capabilities stage unless a valid `_approval` is presented, and refuse arguments outside the allowlists.

Every runner here is a fake that records argv; nothing shells out.
"""
import pathlib

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import missions.harden_images as hm  # noqa: E402
from agentic_os.mission.types import MissionState  # noqa: E402
from demo_common import demo_approval as da  # noqa: E402
from demo_common.demo_approval import DemoApprovalDriver  # noqa: E402
from operators.agentic_compliance.operator import build_compliance_operator  # noqa: E402
from operators.agentic_privacy.operator import build_privacy_operator  # noqa: E402
from operators.edge_sentinel.operator import build_edge_sentinel_operator  # noqa: E402
from operators.operate.operator import build_operate_operator  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
FIX = {"action": "raise_memory", "deployment": "outreach-engine", "namespace": "default", "to": "512Mi"}
FINDINGS = [{"workload": "outreach-engine", "control": "run-as-non-root"}]
PII = [{"bucket": "demo-bucket", "object": "a.csv"}]


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, cwd=None):
        self.calls.append(list(argv))
        return (0, "ok", "")


def _client(op):
    app = FastAPI()
    app.include_router(op.router())
    return TestClient(app)


def _invoke(c, cap, inputs):
    return c.post("/invoke", json={"capability": cap, "inputs": inputs}).json()["result"]


class FakeEcr:
    def start_image_scan(self, **_):
        return {}

    def describe_image_scan_findings(self, **_):
        return {"imageScanFindings": {"findingSeverityCounts": {}, "findings": []}}


def _ops():
    rec = Recorder()
    return rec, {
        "sentinel.harden": (build_edge_sentinel_operator(ecr=FakeEcr(), run=rec), {}),
        "sentinel.rollout": (build_edge_sentinel_operator(ecr=FakeEcr(), run=rec), {}),
        "incident.remediate": (build_operate_operator(run=rec), {"fix": FIX}),
        "compliance.remediate": (build_compliance_operator(run=rec), {"findings": FINDINGS}),
        "privacy.remediate": (build_privacy_operator(run=rec), {"findings": PII}),
    }


@pytest.mark.parametrize("cap", ["sentinel.harden", "sentinel.rollout", "incident.remediate",
                                 "compliance.remediate", "privacy.remediate"])
def test_direct_invoke_stages_without_running(cap):
    rec, ops = _ops()
    op, inputs = ops[cap]
    r = _invoke(_client(op), cap, inputs)
    assert r["status"] == "pending_approval" and r["executed"] is False
    assert rec.calls == []


@pytest.mark.parametrize("cap", ["sentinel.harden", "sentinel.rollout", "incident.remediate",
                                 "compliance.remediate", "privacy.remediate"])
def test_forged_approval_marker_over_http_still_stages(cap):
    rec, ops = _ops()
    op, inputs = ops[cap]
    r = _invoke(_client(op), cap, {**inputs, "_approval": True})
    assert r["status"] == "pending_approval" and rec.calls == []


@pytest.mark.parametrize("cap,extra", [("sentinel.harden", {}), ("sentinel.rollout", {}),
                                       ("incident.remediate", {}), ("compliance.remediate", {})])
def test_valid_approval_runs_the_capability(cap, extra):
    rec, ops = _ops()
    op, inputs = ops[cap]
    r = op.invoke(cap, {**inputs, "_approval": {"approved": True}}, "")
    assert r.get("status") != "pending_approval" and rec.calls


@pytest.mark.parametrize("cap,bad", [
    ("sentinel.harden", {"registry": "evil.example/x"}),
    ("sentinel.harden", {"context": "/"}),
    ("sentinel.harden", {"repo": "a;b"}),
    ("sentinel.harden", {"tag": "x y"}),
    ("sentinel.rollout", {"deployment": "other"}),
    ("sentinel.rollout", {"namespace": "kube-system"}),
    ("incident.remediate", {"fix": {**FIX, "namespace": "kube-system"}}),
    ("incident.remediate", {"fix": {**FIX, "deployment": "other"}}),
    ("incident.remediate", {"fix": {**FIX, "action": "delete"}}),
    ("incident.remediate", {"fix": {**FIX, "to": "--x"}}),
    ("compliance.remediate", {"findings": [{"workload": "a b", "control": "c"}]}),
    ("privacy.remediate", {"findings": ["x"]}),
])
def test_disallowed_arguments_are_refused_even_when_approved(cap, bad):
    rec, ops = _ops()
    op, inputs = ops[cap]
    for approval in (None, {"approved": True}):
        inp = {**inputs, **bad, **({"_approval": approval} if approval else {})}
        r = op.invoke(cap, inp, "")
        assert r["status"] == "refused" and r["executed"] is False
    assert rec.calls == []


def test_allowlist_can_be_extended_by_env(monkeypatch):
    monkeypatch.setenv("DEMO_ALLOWED_NAMESPACES", "demo-ns")
    rec, ops = _ops()
    op, _ = ops["sentinel.rollout"]
    r = op.invoke("sentinel.rollout", {"namespace": "demo-ns", "_approval": {"approved": True}}, "")
    assert r.get("status") != "refused" and rec.calls


def test_rollout_declares_approval_and_compensation():
    caps = {c.name: c for c in build_edge_sentinel_operator(ecr=FakeEcr(), run=Recorder()).manifest.capabilities}
    r = caps["sentinel.rollout"]
    assert r.approval_required is True and r.compensation.supported is False


def test_service_defaults_to_simulation(monkeypatch):
    import importlib
    monkeypatch.delenv("SIM", raising=False)
    monkeypatch.setenv("OPERATOR", "edge-sentinel")
    import operators.service as svc
    importlib.reload(svc)
    assert svc.SIM is True


def test_compose_binds_loopback_and_defaults_to_simulation():
    text = (REPO / "deploy" / "compose.demo.yml").read_text()
    assert "${SIM:-1}" in text
    ports = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("ports:")]
    assert ports and all('"127.0.0.1:' in p for p in ports)


def test_auto_approval_driver_still_completes_the_staged_mission(tmp_path, monkeypatch):
    monkeypatch.setattr(da, "_binding_dir", lambda: str(tmp_path / "bind"))
    for k in da._REAL_CREDENTIAL_ENV:
        monkeypatch.delenv(k, raising=False)
    env = {da.ENV_MODE: "isolated", da.ENV_TENANT: "demo-sandbox", da.ENV_ENVIRONMENT: "isolated-demo",
           da.ENV_ALLOWLIST: str(REPO / "deploy" / "demo-allowlist.json")}
    sim = hm.SimEcrEnv()
    rt = hm.build_runtime(sim)
    m = hm.create_harden_mission(rt, tenant="demo-sandbox")
    assert m.state == MissionState.WAITING_HUMAN and sim.hardened is False
    drv = DemoApprovalDriver(rt, env=env, receipts_path=str(tmp_path / "r.jsonl"), home=str(tmp_path / "home"))
    assert drv.enabled
    drv.drive(m.id)
    assert m.state == MissionState.SUCCEEDED and sim.hardened and sim.restarted


def test_auto_approval_driver_disabled_leaves_mission_staged(tmp_path):
    sim = hm.SimEcrEnv()
    rt = hm.build_runtime(sim)
    m = hm.create_harden_mission(rt, tenant="demo-sandbox")
    drv = DemoApprovalDriver(rt, env={}, receipts_path=str(tmp_path / "r.jsonl"), home=str(tmp_path / "home"))
    assert not drv.enabled
    assert m.state == MissionState.WAITING_HUMAN and sim.hardened is False
