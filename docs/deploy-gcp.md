# GCP deployment (GKE + Artifact Registry)

Hyperscaler-specific deployment branch: `deploy/gcp`. Branches off the shared multi-cloud template
and carries the GCP-only tuning. **Live** on project `gen-lang-client-0690890693` (number
`483251169859`), region `us-central1`, zone `us-central1-a`.

## Authentication — keyless (this org blocks SA keys)
The org enforces `constraints/iam.disableServiceAccountKeyCreation` **and** denies owner-level
service-account IAM edits, so there is **no SA key and no Vault secret for GCP**. Terraform runs on the
owner's short-lived token:

```bash
export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token)
export GOOGLE_PROJECT=gen-lang-client-0690890693
```

(Persistent alternative: `gcloud auth application-default login` as the project owner — ADC works too.)

## Org-lockdown workaround — node service account
This project has **no Compute Engine default service account**
(`<project-number>-compute@developer.gserviceaccount.com`), which GKE nodes normally run as, so
cluster creation fails with *"failed to check status for …-compute@…; Verify if principal exists."*

Fix: point the node identity at a real SA. We created `redevops-demo-deployer` (SA creation is
allowed; only *key* creation is blocked) and granted it the standard node roles at the **project**
level (project IAM works; SA-level IAM is denied):

```
roles/logging.logWriter  roles/monitoring.metricWriter  roles/monitoring.viewer
roles/stackdriver.resourceMetadata.writer  roles/artifactregistry.reader
```

The `envs/gcp` module gained a `node_service_account` variable, applied to **both** the cluster's
throwaway default pool (a `dynamic "node_config"`) and the managed node pool.

## Deploy
Governed mission (keyless), pauses at the human approval gate; `APPROVE=1` applies:

```bash
export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token)
python -m missions.deploy_gcp            # scan → real plan → ⛔ approval gate
APPROVE=1 python -m missions.deploy_gcp  # + apply (creates GKE + Artifact Registry)
```

Or drive the operator's terraform directly with the node SA:

```bash
SA=redevops-demo-deployer@gen-lang-client-0690890693.iam.gserviceaccount.com
terraform -chdir=infra/terraform/envs/gcp apply \
  -var gcp_project=gen-lang-client-0690890693 -var node_service_account=$SA
```

## Current live state
- GKE cluster `redevops-demo-demo` — RUNNING, v1.35.x-gke, 2× `e2-standard-2` nodes.
- 5 Artifact Registry repos (`us-central1-docker.pkg.dev/gen-lang-client-0690890693/redevops-demo-demo-<app>`).
- Reach it: `gcloud container clusters get-credentials redevops-demo-demo --zone us-central1-a --project gen-lang-client-0690890693` (needs the `gke-gcloud-auth-plugin` for kubectl).

## Not yet done
- App deploy onto GKE (`configure`/`verify` steps) — the Ansible/Helm is still EKS-shaped.
- The `agentic-tests → Projects UI (Sidekick) → GCP` browser-driven flow.
