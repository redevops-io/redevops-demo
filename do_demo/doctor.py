"""`python -m do_demo.doctor` — run the DigitalOcean preflight and print the checklist.

Works in two credential modes so no user is forced into Vault (mirrors aws_demo/doctor.py):
  • governed: Vault-held API token (DoDemoCreds) — the team/demo default
  • solo:     ambient env token (DIGITALOCEAN_TOKEN / DIGITALOCEAN_ACCESS_TOKEN) if Vault isn't set
"""
from __future__ import annotations

import os

from demo_common.preflight import Check, Report, check_local, render

from .creds import DOClient, DoDemoCreds
from .preflight import check_do


def session_factory():
    """Return role -> DOClient. Prefer the Vault token; fall back to an ambient env token.

    In solo mode the same client backs every "role" (DO tokens aren't role-scoped). If no token is
    available at all, a token-less client is returned so the preflight reports a clean
    'do-credentials fail' rather than crashing.
    """
    try:
        creds = DoDemoCreds()
        _ = creds.region  # forces a Vault read; raises if Vault/config absent
        return creds.session
    except Exception:
        token = os.environ.get("DIGITALOCEAN_TOKEN") or os.environ.get("DIGITALOCEAN_ACCESS_TOKEN") or ""
        region = os.environ.get("DIGITALOCEAN_REGION") or "nyc3"
        registry = os.environ.get("DIGITALOCEAN_REGISTRY")
        ambient = DOClient(token, region=region, registry_name=registry)
        return lambda role: ambient  # same ambient token for every "role" in solo mode


def main() -> int:
    report = Report(cloud="digitalocean")
    check_local(report, cli="doctl")
    try:
        check_do(report, session_factory())
    except Exception as e:  # noqa: BLE001
        report.add(Check("digitalocean", "fail", f"preflight aborted ({type(e).__name__})",
                         "See docs/getting-started.md for DO account + token setup."))
    print(render(report))
    return 0 if report.ready else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
