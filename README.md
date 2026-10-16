# redevops-demo

[![NVIDIA Inception](https://img.shields.io/badge/NVIDIA-Inception%20Program%20Member-76B900.svg)](https://www.nvidia.com/en-us/startups/)

**What it does.** A governed deploy-and-operate demo: missions plan, gate (human approval), "provision", scan,
harden, and remediate an EKS-shaped stack, with every consequential step gated and replayable.
**Everything runs against in-process simulators ($0, `SIM=1`).** The repo also contains Terraform for AWS
(and Azure/GCP/DigitalOcean) and a real-mode path, but **no real cloud apply is claimed or evidenced here**:
"real `apply`" is listed under **Next**. Despite older wording elsewhere, this repo does **not** currently
provision, secure or self-heal a real EKS stack.

**Who it's for.** People evaluating how Mission Runtime, Context Runtime and Sidekick fit together on a
deploy-and-operate workflow, and contributors to those projects.

**Maturity: simulation-only.** (version `0.0.0`, no tags or releases; see [STATUS.md](STATUS.md).)

**3-minute example** (no cloud, no credentials):
```bash
uv venv .venv && uv pip install --python .venv -e ".[dev]"
.venv/bin/pytest                          # zero cloud calls
SIM=1 .venv/bin/python -m missions.deploy_operate   # simulated deploy-and-operate mission, parks at an approval gate
```

**Evidence.** The test suite under [`tests/`](tests/) (run it yourself; this repo commits no test report, and
the CI workflow is [`.github/workflows/tests.yml`](.github/workflows/tests.yml)). No real `terraform plan`/`apply`
output, cost report or cloud bill is committed.

**Limitations.** Simulated runtime only; numbers quoted below (68 resources, ~$0.31/hr, test counts) are
*Illustrative/synthetic* and not reproducible as real-cloud results from this repo (details under
[Status](#status)). Hosted-demo behaviour is not covered here. Safety notes on `--auto-approve` are below.

**Docs.** [PLAN.md](PLAN.md) · [docs/getting-started.md](docs/getting-started.md) · [docs/aws-permissions.md](docs/aws-permissions.md) · [docs/aws-fit-roadmap.md](docs/aws-fit-roadmap.md) (aspirational) · [STATUS.md](STATUS.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md)

---

A **self-demonstrating**, **multi-cloud** ReDevOps demo: this repo *contains* the Terraform + Ansible
it deploys, so one prompt in **Projects** is designed to deploy it onto a hyperscaler and then **secure, harden,
monitor, and heal** it — every consequential step gated, inspectable, and replayable. *Today this is exercised
only against simulators; see Maturity above.*

The same governed deploy-and-operate spine targets four clouds through one cloud-agnostic
`deployment-preflight` contract; only each cloud's managed-Kubernetes + registry Terraform and its
credential model differ.

| Cloud | Managed k8s + registry | Env | Binding | Status |
|---|---|---|---|---|
| **AWS** | EKS + ECR | `infra/terraform/envs/aws` | `aws_demo/` | **simulated** end to end ($0); Terraform for a trimmed EKS env is in the repo; the "68 resources" figure is from the simulator's canned plan (see [Status](#status)); real `apply` not done (listed under **Next**) |
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
                          ▼  (target: managed k8s — EKS · AKS · GKE · DOKS; simulated in this repo today)
                  managed k8s + Helm monitoring + edge-sentinel + Agentic Compliance/Privacy
```

## Status

**Phase 0 — scaffold + safety rails ($0)** ✅
- Vault → STS **assume-role** cred helper (`aws_demo/creds.py`) — tested (moto), no key leaks
- In-runtime **budget policy + guard** + **out-of-band kill-switch** Terraform (`terraform validate` clean)
- Attachable **IAM policies** (`infra/iam/`) + safety roles + scripts

**Phase 1 — governed deploy spine (sim-first, $0)** ✅
- Trimmed **EKS env + Helm monitoring** Terraform in `infra/terraform/envs/aws`; the "68 resources, $0" figure is the simulator's canned plan text (`missions/deploy_operate.py`), *Illustrative/synthetic*: no real `terraform plan` output is committed
- Real **infra operator** wired to `infra/terraform/envs/aws` (`/invoke` service)
- **Deploy-and-operate mission** runs in-process: scan → plan → **⛔ approval gate** (plan + cost
  evidence, ~$0.31/hr, *Illustrative*: computed by `estimate_cost_from_plan` from the canned plan, not a cloud bill) → provision → configure → verify; "18 tests green" is a historical author-reported count, not reproducible from a committed report
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
  → approve → rebuild+push → rollout → re-scan **cleared** (reject hardens nothing). ("26 tests green": historical author-reported count, not reproducible from a committed report.)
- Run it: `python -m missions.harden_images` (parks at the gate; add `--auto-approve` only in isolated demo mode, see below)

**Phase 3 — operate loop + security posture (sim-first, $0)** ✅
- **Induced-fault operate loop** (`missions/incident_response.py`, the "wow"): rising restarts →
  gather evidence → diagnose (*memory limit too low*) → **⛔ remediation gate** → raise memory →
  **verify healthy** (simulated). Models CloudWatch/Prometheus alarm → incident mission (no silent mutation).
- **Agentic Compliance + Privacy** (`missions/posture.py`): CIS scan of a seeded vulnerable workload;
  PII scan of a synthetic dataset (**inactive with no data source — never fakes findings**). The
  kernel marks the whole compliance/privacy domain **regulatory → even the scan is gated**.
- Run: `python -m missions.incident_response` (parks at the gate; `--auto-approve` only in isolated demo mode, see below). ("34 tests green": historical author-reported count; run `pytest` for the current result.)

**Next:** Phase 4 (Bedrock/AgentCore/Strands + outreach capstone), then the real `apply` for a recorded run (not done yet).

## Quickstart (local, no cloud)
```bash
uv venv .venv && uv pip install --python .venv -e ".[dev]"
.venv/bin/pytest                      # zero cloud calls
```

## Bring the cockpit up (mirrors the one-click guide)
```bash
./scripts/up.sh                       # Projects → http://localhost:8080/cockpit (root compose.yml maps 8080 -> container 8000)
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
Then open the Projects cockpit at http://localhost:8000/cockpit (this `deploy/compose.demo.yml` publishes 8000; the root `compose.yml` used by `scripts/up.sh` publishes 8080) and start the deploy mission — it
plans + scans, pauses at the provision approval gate with the terraform plan + cost estimate, then
provisions → configures → verifies across the federated operators. The `agentic_os` kernel is a pinned pip dependency
(`pip install -e .`); `AGENTIC_OS_ROOT` still overrides it with a source checkout, which is also needed for the
kernel's `apps/` operators (e.g. `infra`), as they are not shipped in the wheel. Defaults to a sibling `agentic-os` checkout.

| service | port | operator |
|---|---|---|
| infra | 8230 | terraform/ansible provision · configure · verify · drift |
| edge-sentinel | 8241 | ECR/Inspector scan → harden → rollout → rescan |
| operate | 8242 | incident: gather → diagnose → remediate → verify |
| agentic-compliance | 8243 | CIS/OpenSCAP posture scan → gated remediation |
| agentic-privacy | 8244 | PII/data-map scan (active only where a data source exists) |
| sidekick | 8000 | Projects cockpit + Mission Runtime governing the above |

## Isolated demo auto-approval (`--auto-approve`)

Every consequential gate in these missions normally waits for a human. For unattended demos,
`demo_common/demo_approval.py::DemoApprovalDriver` can press "approve" for you **only inside an isolated,
disposable demo environment**. It is off by default and fails closed: any doubt leaves the mission parked
for a human and logs why.

> **This is a safety interlock against accidents, NOT a security boundary.** Someone who controls the process
> environment, the policy file or the Python process can defeat it. The real protection is that the demo
> tenant/credentials are disposable, the missions run against simulators, and the kernel's own gates still run.

```bash
export REDEVOPS_DEMO_MODE=isolated
export REDEVOPS_DEMO_TENANT=demo-sandbox                 # must equal the policy's designated tenant
export REDEVOPS_DEMO_ENVIRONMENT=isolated-demo           # must equal the policy's designated environment
export REDEVOPS_DEMO_ALLOWLIST=deploy/demo-allowlist.json
SIM=1 python -m missions.deploy_operate --auto-approve   # banner: ISOLATED DEMO: auto-approving allowlisted simulated actions
```
(`APPROVE=1` is now just an alias of `--auto-approve`; it no longer approves unconditionally.)
The same flag exists on `missions.incident_response` and `missions.harden_images`.

**How isolation is enforced** (all must hold, checked on every request, never cached):
- `REDEVOPS_DEMO_MODE` is exactly `isolated`; unset or anything else = disabled.
- the tenant and environment markers equal the policy's designated values, **and** the mission itself
  was created in that tenant. The designated values are fixed constants in code (`demo-sandbox`,
  `isolated-demo`); a policy may use another target only with `"custom_target": true` and a strict
  `demo-sandbox-<id>` / `isolated-demo-<id>` form. Changing the tenant, the environment, or the action digest
  makes approval fail.
- the allowlist policy is present, valid, unexpired and not more than 400 days out (`expires_at` must be a finite
  timestamp); it lists **exact** capability names. If `REDEVOPS_DEMO_POLICY_KEY` is set, the policy file must
  carry a matching hex HMAC-SHA256 in `<policy>.sig`, otherwise it is refused.
- the runtime is the `$0` simulator (`rt.demo_simulated is True`, i.e. `SIM=1`) and no cloud/Vault/kube
  credentials are present: ~25 env names (`AWS_*` incl. web-identity/container/profile/role, `AZURE_*`/`ARM_*`,
  `GOOGLE_APPLICATION_CREDENTIALS`, `DIGITALOCEAN_*`, `VAULT_ADDR/TOKEN`, `DOCKER_HOST`, `SSH_AUTH_SOCK`,
  `KUBECONFIG`, `KUBERNETES_SERVICE_HOST`) and `~/.aws/credentials`, `~/.kube/config`, `~/.vault-token`.
  (Deliberately strict: run demos from a clean shell/container.)
- the request is bound to one action by `digest = sha256(canonical JSON of mission id, node id, capability,
  tenant, environment, inputs)` (the saved-plan digest is part of the inputs). The request, including its
  timestamps, is HMAC-authenticated with a per-process key, so an edited request is refused, and a request
  cannot be approved after a restart (fails closed: recreate the request). It must address this mission,
  match the live gate, be unexpired (5 min TTL, never past the policy expiry) and be **one-time use**. The
  claim is atomic (file lock around check, intent, kernel approve, result), so two drivers or processes
  sharing a receipts path cannot both approve; a replayed digest is refused across restarts too.
  All processes must share ONE receipts path (it holds the lock and replay state); a second path for the same
  policy file is refused. A gate whose kernel approve failed 3 times is refused (`too_many_failures`) until
  `clear_failures` is called.
- it approves through the same `MissionRuntime.approve` a human uses, with its own identity `demo_driver`.
  The kernel's own gates are untouched: provision still needs the approved `plan_digest`, configure is still
  its own gate, and the runtime still mints the signed approval token the operator verifies. The driver
  cannot forge or bypass any of it.

**Audit.** Each decision (approved or refused) writes a receipt `{actor: demo_driver, policy_version, digest,
decision, ts, mission/node/capability/tenant/environment, reason}`. Two phases: a `DemoAutoApprovalIntent` event
before the kernel's `ApprovalGranted` and a `DemoAutoApprovalResult` (granted | failed + reason) after; refusals
are `DemoAutoApproval` events. All also go as JSON lines to
`$REDEVOPS_DEMO_RECEIPTS` (default `.redevops-demo/receipts.jsonl`). If the receipt cannot be written, nothing is approved.

**Allow-listed today** (`deploy/demo-allowlist.json`, all against the simulators): `infra.provision`,
`infra.configure`, `incident.remediate`, `sentinel.harden`.

**Never auto-approved**: sending real messages (email/SMS/chat), payments, any change to a production system,
any change to real customer records, any real cloud apply (`SIM=0`), and anything not on the allowlist.
In those cases the mission parks and a human approves in the cockpit.

**Extending the allowlist safely.** Add one object per action type to the JSON policy and bump `version`:
`{"capability": "crm.update_simulated", "params": {"account_id": ["acct-fixture-1"]}, "max_cost_usd": 0}`.
Rules: exact capability names only (`*`, `?`, `[]` are rejected at load); declare `params` (input key -> list of
permitted values, or null for any) so literal inputs are not accepted by accident; only add an action whose
operator is backed by a simulator/fixture in the demo tenant; keep `expires_at` short and re-sign by editing the
file in review. Add a test next to `tests/test_demo_approval.py` showing a variation (other tenant, modified
input) is refused.

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
**AWS is the most developed cloud binding, but all clouds are sim-validated only: no cloud has been applied to real infra from this repo's evidence** (Azure/GCP/DigitalOcean: IaC + bindings, `terraform validate` clean).

Safety is layered: **in-runtime** budget guard + approval gates on every consequential step, and an
**out-of-band** budget alarm + auto-destroy that works even if the runtime is down. No cloud keys live in
this repo or in `compose.yml` — Sidekick pulls short-lived, scoped credentials from Vault per cloud.

## License

AGPL-3.0-only. See [LICENSE](LICENSE).
