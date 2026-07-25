"""DigitalOcean binding of the cloud-agnostic **deployment-preflight** skill.

Same ``{ready, checks[], blockers[]}`` contract as the AWS binding, built on ``demo_common.preflight``
primitives — only the CLOUD probes differ. DO has no model-plane (Bedrock) analog, so that probe is
omitted; everything else maps: a token that authenticates, that it can enumerate DOKS + the Container
Registry, and a region check.

Blocker split (mirrors AWS):
  • LOCAL — Docker is the only hard requirement; doctl/terraform/kubectl/helm run inside the operator
    container (handled by ``demo_common.preflight.check_local(report, cli="doctl")`` in the doctor).
  • CLOUD — the token itself is a HARD ``fail`` if it can't authenticate; DOKS enumeration is a HARD
    ``fail`` (can't provision without it); the registry read is a ``warn`` (it may not exist yet).
"""
from __future__ import annotations

from typing import Callable

from demo_common.preflight import Check, Report, probe


def check_do(report: Report, session_factory: Callable[[str], "object"], *, want_region: str = "nyc3") -> None:
    """session_factory(role) -> DOClient. In the demo this is DoDemoCreds.session (same client per role)."""
    # authenticate via the readonly "role" — a cheap authenticated read proves the token works
    try:
        client = session_factory("readonly")
        acct = client.get("/v2/account").get("account", {})
        who = acct.get("email") or acct.get("uuid") or "authenticated"
        report.add(Check("do-credentials", "ok", who))
        region = getattr(client, "region", None)
        report.add(Check(
            "region", "ok" if region == want_region else "warn", f"region={region}",
            "" if region == want_region else f"Default is {want_region}; set config.region if you meant another DO region.",
        ))
    except Exception as e:  # noqa: BLE001
        report.add(Check(
            "do-credentials", "fail", f"token rejected ({type(e).__name__})",
            "Store a valid DO API token in Vault (secret/redevops/do-demo/bootstrap: api_token) or "
            "export DIGITALOCEAN_TOKEN. See docs/getting-started.md.",
        ))
        return  # nothing else will work without a working token

    # DOKS enumeration is a HARD blocker — terraform can't provision the cluster without a read/write token.
    probe(
        report, "perm:deployer(doks)",
        lambda: session_factory("deployer").get("/v2/kubernetes/clusters"),
        "Use a READ/WRITE DO token so terraform can create DOKS (DO tokens are account-wide; scope is by token).",
        severity="fail",
    )
    # Container Registry read is a WARN — the deploy runs without it; the DOCR may simply not exist yet.
    probe(
        report, "perm:readonly(registry)",
        lambda: session_factory("readonly").get("/v2/registry"),
        "Create the DOCR (terraform apply) to push demo images — not required to deploy the cluster.",
        severity="warn",
    )
