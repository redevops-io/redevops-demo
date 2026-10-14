"""The bridge: the deploy mission drives the operators over the HTTP /invoke contract (SIM=0 shape).

Operators run behind FastAPI /invoke (as they do in the compose), driven via HTTPOperatorClient with
a TestClient-backed transport — so the real HTTP path is exercised in-process, $0 (operators in sim).
"""
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import missions.deploy_operate as dop  # noqa: E402
from agentic_os.mission.types import MissionState  # noqa: E402
from missions.harden_images import SimEcrEnv  # noqa: E402
from operators.edge_sentinel.operator import build_edge_sentinel_operator  # noqa: E402
from infra.operator import build_infra_operator  # noqa: E402


def _mount(op) -> TestClient:
    app = FastAPI()
    app.include_router(op.router())
    return TestClient(app)


def test_operator_service_exposes_capabilities_and_invoke():
    c = _mount(build_edge_sentinel_operator(ecr=SimEcrEnv(), run=SimEcrEnv().run))
    caps = c.get("/capabilities").json()
    names = [x["name"] for x in caps["capabilities"]]
    assert "sentinel.scan" in names and "sentinel.harden" in names
    r = c.post("/invoke", json={"capability": "sentinel.scan", "inputs": {}}).json()
    assert r["result"]["needs_hardening"] is True


def _http_deploy():
    # infra + edge-sentinel behind /invoke, in sim
    infra_c = _mount(build_infra_operator(run=dop._sim_runner, http_get=lambda u: 200))
    env = SimEcrEnv()
    sent_c = _mount(build_edge_sentinel_operator(ecr=env, run=env.run))
    clients = {"http://infra": infra_c, "http://edge-sentinel": sent_c}

    def transport(url, body, headers, timeout):
        for base, c in clients.items():
            if url.startswith(base):
                return c.post("/invoke", json=body, headers=headers or {}).json()
        raise KeyError(url)

    urls = {"infra": "http://infra", "edge-sentinel": "http://edge-sentinel"}
    rt = dop.build_http_runtime(urls, transport=transport)
    m = dop.create_deploy_mission(rt)

    # scan + plan ran over HTTP; paused at the provision gate
    assert m.state == MissionState.WAITING_HUMAN
    pend = rt.repo.pending_human(m.id)
    dop.approve_all_gates(rt, m.id)  # provision (with plan_digest) then configure: two mandatory gates
    if m.state not in (MissionState.SUCCEEDED, MissionState.FAILED):
        rt.run(m.id)
    return m


def test_deploy_mission_drives_operators_over_http():
    # hermetic: conftest installs a throwaway AGENTIC_OS_APPROVAL_SECRET shared by the runtime and the operators
    assert _http_deploy().state == MissionState.SUCCEEDED


def test_http_approved_actions_require_the_approval_secret(monkeypatch, tmp_path):
    """Documents the requirement: with no secret in the env AND an empty HOME (no kernel secrets file), the
    signed approval cannot be minted, so operators stage the action instead of executing it (fail closed)."""
    monkeypatch.delenv("AGENTIC_OS_APPROVAL_SECRET", raising=False)
    from agentic_os.mission.approval import _secret_bytes
    assert _secret_bytes() == b""
    assert _http_deploy().state != MissionState.SUCCEEDED
