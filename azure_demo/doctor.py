"""`python -m azure_demo.doctor` — run the preflight and print the checklist.

Works in two credential modes so no user is forced into Vault (mirrors aws_demo/doctor.py):
  • governed: Vault + service-principal (AzureDemoCreds) — the team/demo default
  • solo:     DefaultAzureCredential (az login / managed identity / env vars) if Vault/config isn't set
"""
from __future__ import annotations

import os

from demo_common.preflight import Check, Report, check_local, render

from .creds import AzureDemoCreds, AzureSession
from .preflight import check_azure


def session_factory():
    """Return role -> AzureSession. Prefer Vault SP; fall back to the ambient DefaultAzureCredential."""
    try:
        creds = AzureDemoCreds()
        _ = creds.subscription_id  # forces a Vault read; raises if Vault/config absent
        return creds.session
    except Exception:
        from azure.identity import DefaultAzureCredential

        subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
        location = os.environ.get("AZURE_LOCATION") or os.environ.get("AZURE_REGION") or "eastus"
        ambient = AzureSession(
            credential=DefaultAzureCredential(),
            subscription_id=subscription_id,
            location=location,
            role="ambient",
        )
        return lambda role: ambient  # same ambient creds for every "role" in solo mode


def main() -> int:
    report = Report(cloud="azure")
    check_local(report, cli="az")
    try:
        check_azure(report, session_factory())
    except Exception as e:  # noqa: BLE001
        report.add(Check("azure", "fail", f"preflight aborted ({type(e).__name__})",
                         "See docs/getting-started.md for Azure subscription + credential setup."))
    print(render(report))
    return 0 if report.ready else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
