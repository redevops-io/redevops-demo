"""Executable binding of Sidekick's cloud-agnostic **deployment-preflight** skill (Azure impl).

Same ``{ready, checks[], blockers[]}`` contract as aws_demo/preflight.py, built on the shared
``demo_common.preflight`` primitives (``Report``, ``Check``, ``check_local``, ``probe``, ``render``).
Only the CLOUD checks differ — here they're Azure SP resolution, RBAC permission probes, and region.

Two classes of blocker (identical split to AWS):
  1. LOCAL — only **Docker** is truly required; terraform/az/kubectl/helm run *inside* the operator
     container, so they're optional-if-you-want-them-by-hand (reported as warnings by ``check_local``).
  2. CLOUD — the SP credential must resolve (a cheap authenticated read = the "assume-role works"
     proof, HARD fail if not), a deployer permission probe (can it enumerate AKS), and region.

There is no Bedrock analog on Azure (Azure OpenAI is optional and needs a deployed model to probe),
so that check is intentionally omitted rather than faked. Each check carries a one-line ``fix``.
"""
from __future__ import annotations

from typing import Callable

from demo_common.preflight import Check, Report, probe


def check_azure(
    report: Report,
    session_factory: Callable[[str], "object"],
    *,
    want_region: str = "eastus",
) -> None:
    """session_factory(role) -> AzureSession (see azure_demo.creds.AzureDemoCreds.session)."""
    # ---- credential resolves: list resource groups via the readonly SP (least privilege) ----
    # A successful authenticated read is the Azure equivalent of a working assume-role chain.
    try:
        sess = session_factory("readonly")
        from azure.mgmt.resource import ResourceManagementClient  # lazy: keep SDK optional

        rmc = ResourceManagementClient(sess.credential, sess.subscription_id)
        next(iter(rmc.resource_groups.list()), None)  # forces a real token + ARM call
        report.add(Check("azure-credentials", "ok", f"subscription={sess.subscription_id}"))

        region = getattr(sess, "location", None)
        report.add(Check(
            "region", "ok" if region == want_region else "warn", f"location={region}",
            "" if region == want_region else f"Use {want_region} for broad AKS/Azure-OpenAI availability.",
        ))
    except Exception as e:  # noqa: BLE001
        report.add(Check(
            "azure-credentials", "fail", f"cannot resolve the demo service principal ({type(e).__name__})",
            "Create the SP + RBAC role assignments and store them in Vault "
            "(secret/redevops/azure-demo/{bootstrap,config}). See docs/getting-started.md.",
        ))
        return  # nothing else will work without creds

    # deployer perms are a HARD blocker (can't provision without them): can it enumerate AKS?
    def _list_aks() -> None:
        s = session_factory("deployer")
        from azure.mgmt.containerservice import ContainerServiceClient  # lazy

        csc = ContainerServiceClient(s.credential, s.subscription_id)
        next(iter(csc.managed_clusters.list()), None)

    probe(
        report, "perm:deployer(aks)", _list_aks,
        "Grant the deployer SP Contributor (or an AKS-scoped role) on the subscription/resource group.",
        severity="fail",
    )

    # cost is WARN — the deploy runs without it (cost monitoring is optional)
    def _query_cost() -> None:
        s = session_factory("readonly")
        from azure.mgmt.costmanagement import CostManagementClient  # lazy

        CostManagementClient(s.credential)  # construct-only probe; a full query runs in budget_guard

    probe(
        report, "perm:readonly(cost)", _query_cost,
        "Grant the readonly SP the Cost Management Reader role for budget monitoring (not required to deploy).",
        severity="warn",
    )
