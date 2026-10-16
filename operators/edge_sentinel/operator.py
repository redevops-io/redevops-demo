"""edge-sentinel as a Mission Runtime operator. sentinel.scan also provides `image_scanned`,
so it replaces the Phase-1c placeholder scanner in the deploy_app mission too."""
from __future__ import annotations

import os
import sys
import pathlib

# reuse the kernel SDK (same bootstrap as the deploy mission)
_ROOT = os.environ.get("AGENTIC_OS_ROOT") or str(pathlib.Path(__file__).resolve().parents[2].parent / "agentic-os")
if pathlib.Path(_ROOT).exists() and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agentic_os.mission.operator_sdk import Operator, capability  # noqa: E402

from . import core  # noqa: E402
from operators import guard  # noqa: E402


def build_edge_sentinel_operator(*, ecr=None, run=None) -> Operator:
    run = run or core._run

    _repo = lambda i: i.get("repo", "outreach-engine")

    def _scan(i):
        return core.scan_ecr(_repo(i), i.get("tag", "latest"), ecr=ecr)

    def _harden(i):
        return core.harden_image(_repo(i), i.get("registry", "REGISTRY"), tag=i.get("tag", "latest"),
                                 context=i.get("context", "."), run=run)

    def _rollout(i):
        return core.rollout_restart(i.get("deployment", _repo(i)), i.get("namespace", "default"), run=run)

    def _rescan(i):
        return core.rescan(_repo(i), i.get("tag", "latest"), ecr=ecr)

    def _check_harden(i):
        return (guard.check("registry", i.get("registry", "REGISTRY")) or guard.check("context", i.get("context", "."))
                or guard.check_name("repo", _repo(i)) or guard.check_tag(i.get("tag", "latest")))

    def _check_rollout(i):
        return (guard.check("deployment", i.get("deployment", _repo(i))) or guard.check("namespace", i.get("namespace", "default")))

    return Operator("edge-sentinel", [
        capability("sentinel.scan", _scan, provides=["scan_report", "image_scanned"],
                   permissions=["ecr:read"], estimated_value="high", latency_ms=8000),
        capability("sentinel.harden", guard.guarded("sentinel.harden", _harden, _check_harden, image_hardened=False), provides=["image_hardened"],
                   side_effecting=True, approval_required=True,  # the "harden?" gate
                   permissions=["ecr:write"], estimated_value="high", latency_ms=120000),
        capability("sentinel.rollout", guard.guarded("sentinel.rollout", _rollout, _check_rollout, rollout_restarted=False),
                   provides=["rollout_restarted"], side_effecting=True, approval_required=True,
                   compensation={"supported": False, "reversibility": "IRREVERSIBLE",
                                 "notes": "a restart cannot be undone; it only pulls the current image"},
                   permissions=["k8s:write"], estimated_value="high", latency_ms=60000),
        capability("sentinel.rescan", _rescan, provides=["rescan_verified"],
                   permissions=["ecr:read"], estimated_value="high", latency_ms=8000),
    ])
