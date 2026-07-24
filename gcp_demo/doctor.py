"""`python -m gcp_demo.doctor` — run the preflight and print the checklist.

Works in two credential modes so no user is forced into Vault:
  • governed: Vault + service-account keys (GcpDemoCreds) — the team/demo default
  • solo:     Application Default Credentials (google.auth.default(), i.e. `gcloud auth
              application-default login` or a GOOGLE_APPLICATION_CREDENTIALS key) if Vault isn't set
"""
from __future__ import annotations

import os

from demo_common.preflight import Check, Report, check_local, render

from .creds import GcpDemoCreds
from .preflight import check_gcp


def _default_region() -> str:
    return (
        os.environ.get("GOOGLE_CLOUD_REGION")
        or os.environ.get("CLOUDSDK_COMPUTE_REGION")
        or "us-central1"
    )


def resolve():
    """Return (session_factory, region, project).

    Prefer Vault service-account keys; fall back to ambient ADC (same credential for every role).
    """
    try:
        creds = GcpDemoCreds()
        _ = creds.project_id  # forces a Vault read; raises if Vault/config absent
        return creds.credentials, creds.region, creds.project_id
    except Exception:
        # solo mode: Application Default Credentials — lazily imported so import never hard-fails
        from google.auth import default as adc_default  # type: ignore

        ambient, project = adc_default()
        return (lambda role: ambient), _default_region(), project


def main() -> int:
    report = Report(cloud="gcp")
    check_local(report, cli="gcloud")
    try:
        session_factory, region, project = resolve()
        check_gcp(report, session_factory, region=region, project=project)
    except Exception as e:  # noqa: BLE001
        report.add(Check("gcp", "fail", f"preflight aborted ({type(e).__name__})",
                         "See docs/getting-started.md for GCP project + credential setup."))
    print(render(report))
    return 0 if report.ready else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
