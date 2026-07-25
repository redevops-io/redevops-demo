# redevops-demo

A **self-demonstrating**, **multi-cloud** ReDevOps demo: this repo *contains* the Terraform + Ansible
it deploys, so one prompt in **Projects** deploys it onto a hyperscaler and then **secures, hardens,
monitors, and heals** it — every consequential step gated, inspectable, and replayable.

The same governed deploy-and-operate spine targets four clouds through one cloud-agnostic
`deployment-preflight` contract; only each cloud's managed-Kubernetes + registry Terraform and its
credential model differ.

| Cloud | Managed k8s + registry | Env | Binding | Status |
|---|---|---|---|---|
| **AWS** | EKS + ECR | `infra/terraform/envs/aws` | `aws_demo/` | **deployed + validated** (`terraform plan` = 68 res, $0) |
| **Azure** | AKS + ACR | `infra/terraform/envs/azure` | `azure_demo/` | IaC + binding, **sim-validated** ($0) |
| **GCP** | GKE + Artifact Registry | `infra/terraform/envs/gcp` | `gcp_demo/` | IaC + binding, **sim-validated** ($0) |
| **DigitalOcean** | DOKS + DOCR | `infra/terraform/envs/digitalocean` | `do_demo/` | IaC + binding, **sim-validated** ($0) |

The shared core lives in `demo_common/` (preflight contract + budget policy); run any cloud's checklist
with `python -m demo_common.doctor <aws|azure|gcp|digitalocean>`.

See [`PLAN.md`](PLAN.md) for the full architecture, phases, and decisions.

```
Projects (human control plane)
   │  one sentence
   ▼
Mission Runtime ── governs plan · gate · verify · saga · replay
   │
   ├── Context Runtime ── decides retrieval arm + model per step
   └── Sidekick ──────── DevOps agent: Vault→cloud creds, terraform/ansible/kubectl, monitor loop
                          │
                          ▼  (real managed k8s: EKS · AKS · GKE · DOKS)
                  managed k8s + Helm monitoring + edge-sentinel + Agentic Compliance/Privacy
```

## Status

**Phase 0 — scaffold + safety rails ($0)** ✅
- Vault → STS **assume-role** cred helper (`aws_demo/creds.py`) — tested (moto), no key leaks
- In-runtime **budget policy + guard** + **out-of-band kill-switch** Terraform (`terraform validate` clean)
- Attachable **IAM policies** (`infra/iam/`) + safety roles + scripts

**Phase 1 — governed deploy spine (sim-first, $0)** ✅
- Trimmed **EKS env + Helm monitoring** Terraform — `terraform plan` = **68 real resources, $0**
- Real **infra operator** wired to `infra/terraform/envs/aws` (`/invoke` service)
- **Deploy-and-operate mission** runs in-process: scan → plan → **⛔ approval gate** (plan + cost
  evidence, ~$0.31/hr) → provision → configure → verify; **18 tests green**
- Run it: `SIM=1 python -m missions.deploy_operate`

**Onboarding — Sidekick tells you what's required** ✅
- Preflight is a **cloud-agnostic Sidekick skill** (`deployment-preflight`) that gates every deploy
  mission (node 0), shared across AWS/Azure/GCP/DigitalOcean — only the CLI + Terraform syntax differ.
  The shared `{ready, checks[], blockers[]}` contract lives in `demo_common/preflight.py`; each cloud's
  executable binding is `{aws,azure,gcp,do}_demo/preflight.py` (exposable as an MCP `preflight_check(cloud)`
  tool). Run any cloud's checklist for a ✓/✗ list + the **exact fix** per item:
  ```bash
  python -m demo_common.doctor aws          # or azure | gcp | digitalocean
  ```
- It detects creds, region, per-role permissions, and the **Bedrock account-invoke restriction** (with
  the support-case fix). Hard blockers are only Docker + creds + deployer perms; cost/Bedrock are warnings.
- **Only Docker is required locally** — terraform/aws/ansible/helm/kubectl all run in the operator
  container (macOS/Windows/Linux install matrix in the skill + [docs/getting-started.md](docs/getting-started.md)).

**Phase 2 — edge-sentinel supply-chain hardening (sim-first, $0)** ✅
- `operators/edge_sentinel/` — `sentinel.scan` (ECR image findings; also provides `image_scanned`) ·
  `sentinel.harden` (gated rebuild `--pull` + push) · `sentinel.rollout` (`kubectl rollout restart`) ·
  `sentinel.rescan` (confirm cleared)
- **Governed harden loop** (`missions/harden_images.py`): scan finds a seeded CVE → **⛔ "harden?" gate**
  → approve → rebuild+push → rollout → re-scan **cleared** (reject hardens nothing). **26 tests green.**
- Run it: `APPROVE=1 python -m missions.harden_images`

**Phase 3 — operate loop + security posture (sim-first, $0)** ✅
- **Induced-fault operate loop** (`missions/incident_response.py`, the "wow"): rising restarts →
  gather evidence → diagnose (*memory limit too low*) → **⛔ remediation gate** → raise memory →
  **verify healthy**. Models CloudWatch/Prometheus alarm → incident mission (no silent mutation).
- **Agentic Compliance + Privacy** (`missions/posture.py`): CIS scan of a seeded vulnerable workload;
  PII scan of a synthetic dataset (**inactive with no data source — never fakes findings**). The
  kernel marks the whole compliance/privacy domain **regulatory → even the scan is gated**.
- Run: `APPROVE=1 python -m missions.incident_response`. **34 tests green.**

**Next:** Phase 4 (Bedrock/AgentCore/Strands + outreach capstone), then the real `apply` for a recorded run.

## Quickstart (local, no cloud)
```bash
uv venv .venv && uv pip install --python .venv -e ".[dev]"
.venv/bin/pytest                      # 14 tests, zero cloud calls
```

## Bring the cockpit up (mirrors the one-click guide)
```bash
./scripts/up.sh                       # Projects → http://localhost:8080/cockpit
```

## Run the demo — Sidekick governing the five operators

The deploy-and-operate operators run as their own `/invoke` services (one image,
`operators/service.py`, `OPERATOR` selects which). Sidekick **federates** them from
`operators/modules.yaml` — it discovers each manifest over `GET /capabilities` and drives the
missions over `POST /invoke`, without importing the operator code (see
`agentic-os/deploy/sidekick-devops/federation.py`).

```bash
# $0 dry run — every operator short-circuits to its in-process simulator:
SIM=1 docker compose -f deploy/compose.demo.yml up --build

# Real mode (SIM=0, default): operators shell out to terraform/aws/kubectl.
# Sidekick injects Vault→STS short-lived creds before the provision gate.
docker compose -f deploy/compose.demo.yml up --build
```
Then open the Projects cockpit at http://localhost:8000/cockpit and start the deploy mission — it
plans + scans, pauses at the provision approval gate with the terraform plan + cost estimate, then
provisions → configures → verifies across the federated operators. `AGENTIC_OS_ROOT` defaults to a
sibling `agentic-os` checkout.

| service | port | operator |
|---|---|---|
| infra | 8230 | terraform/ansible provision · configure · verify · drift |
| edge-sentinel | 8241 | ECR/Inspector scan → harden → rollout → rescan |
| operate | 8242 | incident: gather → diagnose → remediate → verify |
| agentic-compliance | 8243 | CIS/OpenSCAP posture scan → gated remediation |
| agentic-privacy | 8244 | PII/data-map scan (active only where a data source exists) |
| sidekick | 8000 | Projects cockpit + Mission Runtime governing the above |

## Cloud prerequisites (owner-provisioned)

Each cloud follows the same shape — a scoped credential in Vault + the demo env under
`infra/terraform/envs/<cloud>`. Pick the cloud you're deploying:

| Cloud | Credential model | Vault path | env |
|---|---|---|---|
| AWS | bootstrap key → **STS assume-role** (deployer/agent/readonly) | `secret/redevops/aws-demo/{bootstrap,config}` | `envs/aws` |
| Azure | **service principal** (tenant/client/secret + subscription) | `secret/redevops/azure-demo/{bootstrap,config}` | `envs/azure` |
| GCP | **service-account key** (+ project) | `secret/redevops/gcp-demo/{bootstrap,config}` | `envs/gcp` |
| DigitalOcean | account **API token** (no role-assumption; scope by token) | `secret/redevops/do-demo/{bootstrap,config}` | `envs/digitalocean` |

For AWS specifically: a dedicated **us-east-1** account with **Bedrock model access + AgentCore**, and
`terraform apply` the **safety** module (`infra/terraform/safety/`) → three roles + Budgets kill-switch.
**AWS is the deployed/validated cloud today; Azure/GCP/DigitalOcean ship as sim-validated IaC + bindings**
(`terraform validate` clean) and haven't been applied to real infra yet.

Safety is layered: **in-runtime** budget guard + approval gates on every consequential step, and an
**out-of-band** budget alarm + auto-destroy that works even if the runtime is down. No cloud keys live in
this repo or in `compose.yml` — Sidekick pulls short-lived, scoped credentials from Vault per cloud.
