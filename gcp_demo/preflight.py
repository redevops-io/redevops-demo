"""Executable binding of Sidekick's cloud-agnostic **deployment-preflight** skill (GCP impl).

Same ``{ready, checks[], blockers[]}`` contract as aws_demo, built on the shared
``demo_common.preflight`` primitives (``Report``, ``Check``, ``check_local``, ``probe``, ``render``).
Only the CLOUD probes differ — GCP credentials, per-role permissions, GKE/Artifact Registry
reachability, and an optional Vertex AI check.

Two classes of blocker (the LOCAL split is handled by ``check_local`` in the doctor):
  1. LOCAL — only **Docker** is truly required; terraform/gcloud/kubectl/helm run *inside* the
     operator container, so they're optional-if-you-want-them-by-hand (reported as warnings).
  2. CLOUD — a service-account credential that resolves (mints a token), deployer permission to
     enumerate GKE (hard) + Artifact Registry (soft), and a region check.

Each check carries a one-line `fix` so the cockpit can render a do-this-next checklist.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from demo_common.preflight import Check, Report, probe


def check_gcp(
    report: Report,
    session_factory: Callable[[str], Any],
    *,
    want_region: str = "us-central1",
    region: Optional[str] = None,
    project: Optional[str] = None,
) -> None:
    """session_factory(role) -> google credentials. In the demo this is GcpDemoCreds.credentials.

    ``region`` is the configured deploy region (config-driven on GCP, not carried on the credential);
    the doctor passes ``GcpDemoCreds.region``. Falls back to a config-driven note when unknown.
    ``project`` overrides the project id when the credential can't self-report it (e.g. ambient ADC).
    """
    if not report.cloud:
        report.cloud = "gcp"

    # ---- credential resolves? mint a token from the readonly SA key (needs NO IAM permission,
    # so it isolates "is the Vault→SA chain wired" from "does the SA have perms"). HARD fail. ----
    try:
        creds = session_factory("readonly")
        project = project or getattr(creds, "project_id", None)
        from google.auth.transport.requests import Request  # type: ignore

        creds.refresh(Request())
        email = getattr(creds, "service_account_email", "service-account")
        report.add(Check("gcp-credentials", "ok", f"{email} (project={project or '?'})"))
    except Exception as e:  # noqa: BLE001
        report.add(Check(
            "gcp-credentials", "fail", f"cannot resolve a demo credential ({type(e).__name__})",
            "Create the demo service account key(s) and store them in Vault "
            "(secret/redevops/gcp-demo/{bootstrap,config}). See docs/getting-started.md.",
        ))
        return  # nothing else will work without a working credential

    # region is config-driven on GCP (not on the credential); compare when the doctor supplies it
    if region is not None:
        report.add(Check(
            "region", "ok" if region == want_region else "warn", f"region={region}",
            "" if region == want_region else f"Use {want_region} for broad GKE/Vertex availability.",
        ))
    else:
        report.add(Check("region", "ok", f"config-driven (target {want_region})"))

    # deployer perms to enumerate GKE are a HARD blocker (can't provision the cluster without them)
    probe(
        report, "perm:deployer(gke)",
        lambda: _list_gke_clusters(session_factory("deployer"), project),
        "Grant roles/container.admin (or container.clusters.list) to the deployer service account.",
        severity="fail",
    )
    # Artifact Registry enumerate is WARN — the cluster deploys without it (it's the image plane)
    probe(
        report, "perm:deployer(artifact-registry)",
        lambda: _list_ar_repos(session_factory("deployer"), project, region or want_region),
        "Grant roles/artifactregistry.admin (or artifactregistry.repositories.list) to the deployer SA "
        "(needed to push demo images; not required to stand up the cluster).",
        severity="warn",
    )
    # Vertex AI is OPTIONAL — a soft reachability probe; the demo runs on your existing model plane
    _probe_vertex(report, session_factory, region or want_region, project)


def _list_gke_clusters(creds: Any, project: Optional[str]) -> None:
    from google.cloud import container_v1  # type: ignore

    client = container_v1.ClusterManagerClient(credentials=creds)
    client.list_clusters(parent=f"projects/{project}/locations/-")


def _list_ar_repos(creds: Any, project: Optional[str], region: str) -> None:
    from google.cloud import artifactregistry_v1  # type: ignore

    client = artifactregistry_v1.ArtifactRegistryClient(credentials=creds)
    # consume one page to force the RPC
    next(iter(client.list_repositories(parent=f"projects/{project}/locations/{region}")), None)


def _probe_vertex(report: Report, session_factory: Callable[[str], Any], region: str, project: Optional[str]) -> None:
    """Soft Vertex reachability check — warn on failure, never a blocker."""
    try:
        from google.cloud import aiplatform_v1  # type: ignore

        client = aiplatform_v1.ModelServiceClient(
            credentials=session_factory("agent"),
            client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"},
        )
        next(iter(client.list_models(parent=f"projects/{project}/locations/{region}")), None)
        report.add(Check("vertex-ai", "ok", "reachable"))
    except Exception as e:  # noqa: BLE001
        report.add(Check(
            "vertex-ai", "warn", f"not verified ({type(e).__name__})",
            "Optional — enable the Vertex AI API + grant roles/aiplatform.user to the agent SA. "
            "The demo runs on your existing model plane until then.",
        ))
