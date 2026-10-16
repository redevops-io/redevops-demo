"""Serve any of our operators as a Mission Runtime `/invoke` HTTP service.

One image, selected by env:  OPERATOR=infra|edge-sentinel|operate|agentic-compliance|agentic-privacy
Mounts GET /capabilities + POST /invoke (via the operator's router) + GET /health. Simulation by
default (SIM=1, in-process sims, no cloud calls); SIM=0 opts into terraform/aws/kubectl.

Run:  OPERATOR=edge-sentinel uvicorn operators.service:app --port 8241
"""
from __future__ import annotations

import os
import pathlib
import sys

_ROOT = os.environ.get("AGENTIC_OS_ROOT") or str(pathlib.Path(__file__).resolve().parents[2] / "agentic-os")
for _p in (_ROOT, os.path.join(_ROOT, "apps"), str(pathlib.Path(__file__).resolve().parents[1])):
    if pathlib.Path(_p).exists() and _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI  # noqa: E402

OPERATOR = os.environ.get("OPERATOR", "infra")
SIM = os.environ.get("SIM", "1") == "1"  # default 1 → in-process simulators (no cloud calls, $0); SIM=0 → real


def build_operator():
    if OPERATOR == "infra":
        from infra.operator import build_infra_operator
        if SIM:
            from missions.deploy_operate import _sim_runner
            return build_infra_operator(run=_sim_runner, http_get=lambda u: 200)
        return build_infra_operator()
    if OPERATOR == "edge-sentinel":
        from operators.edge_sentinel.operator import build_edge_sentinel_operator
        if SIM:
            from missions.harden_images import SimEcrEnv
            env = SimEcrEnv()
            return build_edge_sentinel_operator(ecr=env, run=env.run)
        return build_edge_sentinel_operator()
    if OPERATOR == "operate":
        from operators.operate.operator import build_operate_operator
        if SIM:
            from missions.incident_response import SimIncidentEnv
            env = SimIncidentEnv()
            return build_operate_operator(env=env, run=env.run)
        return build_operate_operator()
    if OPERATOR == "agentic-compliance":
        from operators.agentic_compliance.operator import build_compliance_operator
        if SIM:
            from missions.posture import SimComplianceEnv
            env = SimComplianceEnv()
            return build_compliance_operator(env=env, run=env.run)
        return build_compliance_operator()
    if OPERATOR == "agentic-privacy":
        from operators.agentic_privacy.operator import build_privacy_operator
        if SIM:
            from missions.posture import SimPrivacyEnv
            env = SimPrivacyEnv(with_dataset=True)
            return build_privacy_operator(env=env, run=env.run)
        return build_privacy_operator()
    raise SystemExit(f"unknown OPERATOR '{OPERATOR}'")


op = build_operator()
app = FastAPI(title=f"{OPERATOR} operator")
app.include_router(op.router())


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "operator": OPERATOR}
