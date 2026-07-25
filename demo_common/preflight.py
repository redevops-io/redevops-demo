"""Cloud-agnostic core of the **deployment-preflight** skill.

Every cloud binding (aws_demo, azure_demo, gcp_demo, do_demo) implements the same
``{ready, checks[], blockers[]}`` contract on top of these primitives — only the CLOUD checks
(credentials, per-role permissions, managed-Kubernetes reachability, model access) differ. The LOCAL
checks (Docker is the only hard requirement; terraform/kubectl/helm/ansible run inside the operator
container) are shared verbatim here.

A cloud module provides ``check_<cloud>(report, session_factory, ...)`` and a ``render``-friendly
title; the doctor dispatcher (``python -m demo_common.doctor <cloud>``) wires local + cloud checks.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

# tools the demo uses; docker is the only hard local requirement (the rest run in the operator image)
CONTAINER_TOOLS = ["terraform", "kubectl", "helm", "ansible"]


@dataclass
class Check:
    name: str
    status: str  # "ok" | "fail" | "warn"
    detail: str = ""
    fix: str = ""


@dataclass
class Report:
    cloud: str = ""
    checks: List[Check] = field(default_factory=list)

    @property
    def blockers(self) -> List[Check]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def ready(self) -> bool:
        return not self.blockers

    def add(self, c: Check) -> None:
        self.checks.append(c)


def version(tool: str) -> Optional[str]:
    if not shutil.which(tool):
        return None
    for flag in ("version", "--version"):
        try:
            out = subprocess.run([tool, flag], capture_output=True, text=True, timeout=6)
            line = (out.stdout or out.stderr).strip().splitlines()
            if line:
                return line[0]
        except Exception:
            pass
    return shutil.which(tool)


def check_local(report: Report, *, cli: str = "") -> None:
    """Shared local-tool checks. ``cli`` is the cloud's own CLI (aws/az/gcloud/doctl), reported as an
    optional warning like the other container tools."""
    docker = version("docker")
    report.add(Check(
        "docker", "ok" if docker else "fail", docker or "not installed",
        "" if docker else "Install Docker — https://docs.docker.com/get-docker/ (the ONLY hard local requirement).",
    ))
    for t in CONTAINER_TOOLS + ([cli] if cli else []):
        v = version(t)
        report.add(Check(
            t, "ok" if v else "warn", v or "not installed locally",
            "" if v else f"Optional — {t} runs inside the operator container; install locally only to run it by hand.",
        ))


def probe(report: Report, label: str, fn, fix: str, *, severity: str = "fail") -> None:
    """Run a permission/reachability probe; record ok / warn / fail with a one-line fix."""
    try:
        fn()
        report.add(Check(label, "ok", "allowed"))
    except Exception as e:  # noqa: BLE001
        report.add(Check(label, severity, f"denied ({type(e).__name__})", fix))


def render(report: Report) -> str:
    icon = {"ok": "✓", "fail": "✗", "warn": "•"}
    title = f"ReDevOps demo — preflight ({report.cloud})" if report.cloud else "ReDevOps demo — preflight"
    lines = [title, "=" * len(title)]
    for c in report.checks:
        lines.append(f"  {icon.get(c.status, '?')} {c.name:22s} {c.detail}")
        if c.fix and c.status != "ok":
            lines.append(f"      → {c.fix}")
    lines.append("")
    lines.append("READY ✓ — you can run the deploy mission." if report.ready
                 else f"BLOCKED — resolve {len(report.blockers)} item(s) above, then re-run the doctor.")
    return "\n".join(lines)
