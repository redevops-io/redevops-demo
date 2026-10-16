"""Shared input allowlists and approval staging for the demo operators' write capabilities.

A write capability stages (`pending_approval`, `executed: False`) unless the runtime delivered a valid
`_approval`; arguments that would reach a command line are checked against allowlists first and refused
when they do not match. Defaults cover the demo workload; extend them with comma-separated env vars
(DEMO_ALLOWED_REGISTRIES, DEMO_ALLOWED_CONTEXTS, DEMO_ALLOWED_NAMESPACES, DEMO_ALLOWED_DEPLOYMENTS).
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

_ROOT = os.environ.get("AGENTIC_OS_ROOT") or str(pathlib.Path(__file__).resolve().parents[2].parent / "agentic-os")
if pathlib.Path(_ROOT).exists() and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agentic_os.mission.operator_sdk import pending_approval  # noqa: E402

_DEFAULTS = {
    "registry": ("DEMO_ALLOWED_REGISTRIES", ("REGISTRY",)),
    "context": ("DEMO_ALLOWED_CONTEXTS", (".",)),
    "namespace": ("DEMO_ALLOWED_NAMESPACES", ("default",)),
    "deployment": ("DEMO_ALLOWED_DEPLOYMENTS", ("outreach-engine",)),
}
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
_MEMORY = re.compile(r"^[1-9][0-9]{0,5}(Mi|Gi)$")
_CONTROL = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_FIX_ACTIONS = ("raise_memory", "rollback")


def allowed(kind: str) -> tuple:
    env, default = _DEFAULTS[kind]
    extra = tuple(v.strip() for v in os.environ.get(env, "").split(",") if v.strip())
    return default + extra


def check(kind: str, value) -> "str | None":
    """None when `value` is allowed for `kind`, else a short reason."""
    if not isinstance(value, str) or value not in allowed(kind):
        return f"{kind} not allowed"
    return None


def check_tag(value) -> "str | None":
    return None if isinstance(value, str) and _TAG.match(value) else "tag not allowed"


def check_name(label: str, value) -> "str | None":
    return None if isinstance(value, str) and _NAME.match(value) else f"{label} not allowed"


def check_fix(fix) -> "str | None":
    if not fix:
        return None
    if not isinstance(fix, dict) or fix.get("action") not in _FIX_ACTIONS:
        return "fix action not allowed"
    err = check("deployment", fix.get("deployment")) or check("namespace", fix.get("namespace", "default"))
    if err:
        return err
    if fix["action"] == "raise_memory" and not (isinstance(fix.get("to"), str) and _MEMORY.match(fix["to"])):
        return "memory value not allowed"
    return None


def check_workload_findings(findings) -> "str | None":
    if findings is not None and not isinstance(findings, list):
        return "findings not allowed"
    for f in findings or []:
        if not isinstance(f, dict) or check_name("workload", f.get("workload")) \
                or not (isinstance(f.get("control"), str) and _CONTROL.match(f["control"])):
            return "finding not allowed"
    return None


def refused(action: str, reason: str, **preview) -> dict:
    return {"status": "refused", "action": action, "executed": False, "ok": False, "error": reason, **preview}


def guarded(action: str, handler, validate, **preview):
    """Wrap a write handler: refuse disallowed inputs, stage without `_approval`, else run `handler`."""
    def _handler(inp: dict) -> dict:
        err = validate(inp)
        if err:
            return refused(action, err, **preview)
        if not inp.get("_approval"):
            return pending_approval(action, **preview)
        return handler(inp)
    _handler.__name__ = getattr(handler, "__name__", "guarded_handler")
    return _handler
