from __future__ import annotations
import os, pathlib, sys
_ROOT = os.environ.get("AGENTIC_OS_ROOT") or str(pathlib.Path(__file__).resolve().parents[2].parent / "agentic-os")
if pathlib.Path(_ROOT).exists() and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from agentic_os.mission.operator_sdk import Operator, capability  # noqa: E402
from . import core  # noqa: E402
from operators import guard  # noqa: E402


def build_privacy_operator(*, env=None, run=None) -> Operator:
    run = run or (env.run if env is not None else core._run)

    def _scan(i):
        r = core.scan_pii(getattr(env, "buckets", None) if env is not None else i.get("buckets"))
        if env is not None:
            env.findings = r["findings"]
        return r

    def _findings(i):
        return getattr(env, "findings", None) if env is not None else i.get("findings")

    def _remediate(i):
        return core.remediate_pii(_findings(i), run=run)

    def _check(i):
        f = _findings(i)
        if f is not None and not (isinstance(f, list) and all(
                isinstance(x, dict) and isinstance(x.get("bucket"), str) and isinstance(x.get("object"), str) for x in f)):
            return "finding not allowed"
        return None

    return Operator("agentic-privacy", [
        capability("privacy.scan", _scan, provides=["privacy_findings"],
                   permissions=["privacy:read"], estimated_value="high", latency_ms=12000),
        capability("privacy.remediate",
                   guard.guarded("privacy.remediate", _remediate, _check, privacy_remediated=False), provides=["privacy_remediated"],
                   side_effecting=True, approval_required=True, permissions=["s3:write"],
                   estimated_value="high", latency_ms=30000),
    ])
