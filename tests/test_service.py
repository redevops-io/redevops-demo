"""operators/service.py builds every operator (SIM=1) and serves it over /capabilities + /invoke.

This is the exact seam the demo compose runs: one image, OPERATOR selects the operator, SIM=1 wires
the in-process simulator. Guards against a sim-wiring regression in any of the five.
"""
import importlib

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def control_headers() -> dict:
    """The caller credential an operator's /invoke requires (kernel >= v0.4.0); conftest sets a throwaway token for every test."""
    import os
    return {"Authorization": "Bearer " + os.environ["AGENTIC_OS_CONTROL_TOKEN"]}

CASES = {
    "infra": {"infra.plan", "infra.provision", "infra.verify"},
    "edge-sentinel": {"sentinel.scan", "sentinel.harden"},
    "operate": {"incident.gather", "incident.diagnose"},
    "agentic-compliance": {"compliance.scan"},
    "agentic-privacy": {"privacy.scan"},
}


def _client(operator, monkeypatch):
    monkeypatch.setenv("OPERATOR", operator)
    monkeypatch.setenv("SIM", "1")
    import operators.service as svc
    importlib.reload(svc)  # re-read OPERATOR/SIM from env
    app = FastAPI()
    app.include_router(svc.op.router())
    return TestClient(app)


@pytest.mark.parametrize("operator,expected", CASES.items())
def test_operator_builds_and_exposes_capabilities(operator, expected, monkeypatch):
    c = _client(operator, monkeypatch)
    names = {x["name"] for x in c.get("/capabilities").json()["capabilities"]}
    assert expected <= names


def test_edge_sentinel_scan_invokes_over_service(monkeypatch):
    c = _client("edge-sentinel", monkeypatch)
    r = c.post("/invoke", json={"capability": "sentinel.scan", "inputs": {}}, headers=control_headers()).json()
    assert r["result"]["needs_hardening"] is True
