# ReDevOps × AWS — Real-Time Deploy-and-Operate Demo — Build Plan (v2)

**Status:** proposal for review · no code/provisioning until approved
**Home:** `redevops-demo` (new repo — and, self-referentially, the thing the demo deploys)
**Decisions locked:** reuse real **EKS**; **new AWS account/role** for Bedrock/AgentCore (later phase); new demo repo; plan-first; **AWS creds resolved from local Vault**.

---

## 1. The demo, as an on-camera script

The repo is **self-demonstrating**: `redevops-demo` *contains* the Terraform + Ansible that Sidekick deploys, so the demo deploys itself onto AWS and then operates itself.

1. **Bring up Projects + Sidekick** via the one-click guide (`redevops.io/projects` → `docker compose up` / `helm install`). Cockpit opens.
2. **Prompt Sidekick:** *"Deploy the `redevops-demo` repo on AWS."* Sidekick resolves **AWS creds from the local Vault**, inspects the repo, recommends the architecture, estimates cost, and **pauses at the approval gate**.
3. **Approve → provision + deploy.** Terraform stands up **EKS** (+ ECR, EFS, Secrets); a **self-managed monitoring stack is Helm-installed** (kube-prometheus-stack + Loki/Promtail — *not* CloudWatch-managed); the demo workloads roll out, including **edge-sentinel** and the security/compliance operators.
4. **Sidekick reports what rolled out** and asks *"Should I monitor this deployment continuously?"* → user: **"yes"** → the standing monitor + response-mission loop arms against the Helm monitoring services.
5. **edge-sentinel inspects every image** pushed to ECR and used by the deployment (ECR enhanced scanning / Inspector findings + its own analysis) and **reports results** in Projects.
6. **Sidekick asks** *"Do any of these images need hardening?"* → user: **"yes"**.
7. **Hardening loop:** affected images are **rebuilt + pushed to ECR**; Sidekick runs `kubectl rollout restart` on the affected Deployments; edge-sentinel re-scans to confirm the findings cleared. (Consequential steps gated.)
8. **Security & compliance operate loop:** **Agentic Compliance** and **Agentic Privacy** monitor cluster health + posture using the Helm monitoring services, tied into the **AWS AI stack** (below). An induced fault (memory-limit → OOM restarts) is detected → incident mission → diagnose → propose → approve → remediate → verify.
9. **Optional apps are prompt-driven:** if the user names extra apps in the prompt, Sidekick deploys them **and their dependencies**; otherwise only the security spine ships.
10. **New goals, live:** with the stack up and monitored, the user defines fresh goals in Mission Runtime via Projects — e.g. *"deploy our outreach demo for real outreach use"* — and the stack comes up **functional, monitored, and self-healing** for issues, per the user's permissions.

The "wow": one sentence → a real AWS deployment that **secures, hardens, monitors, and heals itself**, every consequential step gated and inspectable, and then keeps accepting new missions.

## 2. Component boundaries (who owns what)

| Component | Role |
|---|---|
| **Projects** | Human control plane: mission graph · arch rec · cost estimate · Terraform plan · approvals · world state · **image-scan + compliance + privacy reports** · incidents · per-node EXPLAIN · rollback/replay |
| **Mission Runtime** | Authoritative state machine for the whole operation (plan · gate · permissions · budget · evidence · retries · rollback · replay · monitor-triggered response missions). Owns deployment state — not Strands/CrewAI. |
| **Sidekick** | DevOps engineer + supervisor: interprets the prompt, **pulls creds from Vault**, inspects the repo, drives Terraform/Ansible/`kubectl`, reports rollout, asks the monitor/harden questions, becomes the standing ops agent |
| **Context Runtime** | Decides *which* intelligence path + model per step; routes retrieval across Bedrock KB + ReDevOps-RAG + CloudWatch/Prometheus + Cost Explorer + Mission Event Store |
| **edge-sentinel** | Supply-chain + SOC operator: scans ECR images, correlates findings, proposes hardening; already exists as a CR tenant (`integrations/edge_sentinel.py`) + agentic app |
| **Agentic Compliance** | Posture/benchmark scanning (CIS/OpenSCAP) over the cluster + workloads; retrieves AWS standards from Bedrock KB; flags + proposes gated remediation |
| **Agentic Privacy** | PII/data-exposure scanning over S3/RDS; ties to AWS Macie + Bedrock Guardrails; flags + proposes gated redaction/policy. **Active only where a real data source exists — else installed & ready, no empty findings.** |
| **Strands** | Model-driven agent loop *inside bounded nodes* (repo analysis, incident diagnosis, log/scan investigation) — behind `/invoke`, never above Mission Runtime |
| **AgentCore** | Managed substrate hosting the Sidekick/Strands + specialist agents; Gateway (APIs→MCP), Identity, Observability, Evaluations |
| **Bedrock / Bedrock KB** | Model provider (Context Runtime selects per task class) + one managed retrieval arm (AWS compliance/security corpora, runbooks) |

**AWS AI stack tie-ins (concrete):**
- **edge-sentinel** ↔ ECR enhanced scanning + **Amazon Inspector** findings as evidence.
- **Agentic Compliance** ↔ **AWS Security Hub / Config** signals + **Bedrock KB** (CIS/AWS Well-Architected/compliance corpora) + Bedrock reasoning for remediation drafting.
- **Agentic Privacy** ↔ **Amazon Macie** (S3 PII discovery) + **Bedrock Guardrails** (PII/content) for classification.
- **Sidekick diagnosis** ↔ **Strands on AgentCore Runtime**, tools via **Gateway**, models via **Bedrock** chosen by Context Runtime.

## 3. Exists vs greenfield (grounded)

**EXISTS — reuse:**
- Mission kernel + `deploy_app`/`teardown_app`/`cost_audit` templates + **real** Terraform/Ansible driver (`agentic-os/apps/infra/core.py`), external-agent `/invoke` seam (`mission/operators.py`), cockpit (`mission/api.py`), monitor→response-mission loop (`deploy/sidekick-devops/monitor.py`).
- Real **EKS IaC** + ~27 Ansible playbooks (`ffmpeg-mcp-aws/aws/terraform` + `ansible/`) incl. `build-and-push-ecr.yml`.
- **edge-sentinel** — CR tenant `contextos/context_runtime/integrations/edge_sentinel.py` (SOC triage, approval-gated `BlockIpTool`) + agentic app.
- **Agentic Privacy** — `CR-enterprise/apps/services/agentic-privacy/core.py` (boto3 S3 already).
- **Vault** — already deployed locally + used by the stack (`vibexgen/agentic-os/stack`, `agentic-render-env`); cred-resolution pattern exists.
- Context Runtime routing core (bandit/quality-ledger/costmodel/calibration/EXPLAIN); `modules.py` already has `incident`(logs/git/metrics/runbook) + `finance` tenants — the CloudWatch/Cost-Explorer hooks.
- **Outreach demo** (from the last screencast) — the post-deploy "new goal" target; already a working stack.

**GREENFIELD — build:**
1. `redevops-demo` self-referential **Terraform+Ansible** (EKS + ECR + **Helm monitoring stack** + workloads), wired to the real infra operator behind `/invoke`.
2. **Vault→Sidekick cred resolution** for AWS (read-only handle, never printed).
3. Real node logic for `inspect_repo` / `recommend_architecture` / `estimate_cost` (today stubs).
4. **ECR image-scan operator** (edge-sentinel wired to ECR/Inspector) + the **harden→rebuild→push→`kubectl rollout restart`→re-scan** loop.
5. **Agentic Compliance** operator (CIS/OpenSCAP + Security Hub/Config + Bedrock KB) — new; **Agentic Privacy** AWS wiring (Macie/Guardrails) — new on top of the existing S3 core.
6. **AWS-native monitor triggers** (Prometheus/Alertmanager + optional CloudWatch) → incident response mission; induced-fault app variant.
7. **Context Runtime AWS arms**: Bedrock `Tier`; `store_bedrock_kb.py`; CloudWatch/Prometheus + Cost Explorer `ToolPlugin`s; Mission Event Store retriever.
8. **Sidekick-on-Strands** node + **AgentCore** hosting + one **CrewAI** operator (framework-neutrality) — needs the new Bedrock/AgentCore account.
9. **Prompt-driven optional-app expansion** (Sidekick resolves named apps + deps into the mission plan).

## 4. Repo layout (`redevops-demo`)

```
redevops-demo/
  PLAN.md · README.md · compose.yml         ← Projects + Mission Runtime + infra operator + Context Runtime
  infra/
    terraform/envs/eks/                      ← self: VPC+EKS+ECR+EFS+Secrets, budget-tagged+TTL
    terraform/monitoring/                     ← Helm releases: kube-prometheus-stack, loki-stack
    ansible/                                  ← build-push-ecr, deploy-workloads, harden-image, induced-fault variant
  operators/
    infra_operator/                           ← real terraform/ansible/kubectl driver, /invoke
    edge_sentinel/                            ← ECR scan + hardening proposals, /invoke
    agentic_compliance/  agentic_privacy/     ← posture + PII operators, /invoke
    sidekick_strands/  crewai_arch_review/    ← later-phase agents, /invoke
  context_arms/                               ← bedrock_kb, cloudwatch/prometheus, cost_explorer, mission_event_store
  missions/deploy_operate.py                  ← template wiring, policy grants, budgets, optional-app expansion
  monitor/aws_monitor.py                      ← Prometheus/CloudWatch alarm → incident response mission
  vault/                                      ← cred-resolution helper (reads AWS handle from local Vault)
  tests/                                      ← moto + kind; full flow at $0
  scripts/ up.sh · teardown.sh · budget-guard.sh
```
Kernel + Context Runtime pinned as deps (submodule/vendored), not forked.

## 5. Phased plan

### Phase 0 — scaffold + safety rails ($0 build; needs the account for the alarm)
Repo skeleton; pin kernel + Context Runtime; `compose.yml` brings Projects+Sidekick up locally (mirrors the one-click guide). **Vault cred-resolution helper:** reads `aws-demo/bootstrap` + `aws-demo/config`, **assumes deploy/agent/readonly roles via STS** per task, hands the mission a short-lived session (never prints keys). Hard mission **USD budget** + `budget-guard.sh` (in-runtime auto-teardown). **Out-of-band kill-switch** (see §6): account Budgets alarm + `emergency-destroy.sh` + Budgets→SNS→Lambda, provisioned once in the new account. All resources tagged `demo=aws` + TTL. `pytest` (moto/kind) green, no cloud calls.
**Prereq you provide:** the dedicated us-east-1 account + the three IAM roles + `bootstrap`/`config` written to Vault. (I'll ship the role trust-policy + Terraform for the roles/alarm as reviewable code; you apply it with account-owner creds.)

### Phase 1 — governed self-deploy (sim-first → real EKS)
Populate `infra/terraform/envs/eks` + `terraform/monitoring` (Helm) + `ansible/`; wire the real infra operator. Implement `inspect_repo` / `recommend_architecture` / `estimate_cost`; run the deploy mission to the **approval gate** with a real cost estimate + plan as evidence; approve → provision EKS + Helm monitoring + workloads (edge-sentinel + security spine) → Sidekick **reports rollout** → **"monitor?" → yes** arms the loop. `teardown_app` cleanly destroys. Sim-first via moto/`terraform plan` + `kind`, then one real recorded run.
**Acceptance:** cockpit shows the gated mission; approve → real EKS with Helm Prometheus/Grafana/Loki + edge-sentinel running; monitor armed; EXPLAIN per node; teardown clean.

### Phase 2 — supply-chain hardening (edge-sentinel)
edge-sentinel scans ECR images (Inspector + analysis) → **report in Projects**; Sidekick asks **"harden?" → yes** → rebuild+push affected images → `kubectl rollout restart` → re-scan confirms cleared. Gated at push+restart.
**Acceptance:** findings shown → approve → images rebuilt+rolled → re-scan green; the whole loop replays.

### Phase 3 — security/compliance + the operate "wow"
**Agentic Compliance** (CIS/OpenSCAP over cluster+workloads, Security Hub/Config + Bedrock KB) and **Agentic Privacy** (Macie/Guardrails over S3/RDS) run against the Helm monitoring services and post gated findings — **real scanners against the seeded conditions from §7.3** (one intentionally-vulnerable workload + a synthetic fake-PII S3 dataset), so the recording is reproducible without faking output. Induced fault (memory-limit → restarts) → Prometheus alarm → **incident mission**: gather logs+metrics+history+runbook → Strands diagnoses → Context Runtime selects evidence+model → verify → simulate fix (raise memory / rollback) → **approval** (impact+cost) → remediate → re-verify → archive as learning.
**Acceptance:** restarts rise → incident auto-opens → evidence+fix+cost in Projects → approve → healed; compliance/privacy findings visible + gated.

### Phase 4 — extensible goals (needs the new Bedrock/AgentCore account)
Prompt-driven **optional-app expansion** (Sidekick resolves named apps + deps). Context Runtime **cost-based routing across Bedrock KB + ReDevOps-RAG + CloudWatch + Cost Explorer + Event Store** with EXPLAIN. Sidekick-on-**Strands** hosted on **AgentCore**; one **CrewAI** `architecture_review` operator. Then the capstone: user types a **new goal in Projects** — *"deploy the outreach demo for real"* — and Mission Runtime brings it up **functional + monitored + self-healing** under the same governance.
**Acceptance:** a second, user-defined mission (outreach) deploys onto the running stack, is monitored, and an induced issue is auto-remediated per the user's permissions; benchmark shows Context Runtime choosing cheaper-sufficient arm+model per step.

## 6. Cost & safety guardrails
- **In-runtime:** hard USD budget (simulator blocks over-budget plans); every consequential step approval-gated (provision · harden-push · remediate · teardown — kernel-enforced); real infra tagged + TTL'd; `budget-guard.sh` auto-opens a teardown mission past cap; **Vault handles never printed**; short-lived assumed roles only; `moto`+`kind` for $0 CI; `teardown_app` every session.
- **Out-of-band kill-switch (survives an unhealthy stack — REQUIRED):** an **account-level AWS Budgets alarm** plus a standalone **emergency-destroy** path (Budgets → SNS → Lambda auto-teardown, and a hand-runnable `scripts/emergency-destroy.sh` = `terraform destroy` / `eksctl delete cluster`) that lives **entirely outside** Mission Runtime, Sidekick and EKS. If the runtime, the cluster, or Sidekick itself is down, the account still tears itself down at the cap.

## 7. Decisions (resolved 2026-07-22)

1. **Default-on core + security spine (kept small).** Always deployed: **Context Runtime, Mission Runtime, Sidekick, infra operator** (nothing can plan/provision/explain/remediate without them), **Helm monitoring, edge-sentinel, Agentic Compliance, Agentic Privacy**, and the **monitor/response loop**. Every business-facing app is **opt-in via the prompt** — the first deployment stays understandable while still proving the full secure-operate loop.
   - **Privacy caveat:** active only where a **real data source exists**; otherwise shown as *installed & ready*, never manufacturing empty findings.

2. **Dedicated AWS demo account · us-east-1.** A brand-new account (not a role in prod) holds the entire public demo — EKS, Bedrock, AgentCore — for clean billing, teardown, permissions and blast-radius isolation. us-east-1 (broad Bedrock + AgentCore Runtime/Gateway availability).
   **Vault layout — one bootstrap credential + role assumption (role assumption *is* the security boundary):**
   ```
   secret/redevops/aws-demo/bootstrap   access_key_id · secret_access_key      (assume-role only)
   secret/redevops/aws-demo/config      account_id · region · deploy_role_arn · agent_role_arn · readonly_role_arn
   roles:
     redevops-demo-deployer    Terraform / EKS / ECR provisioning
     redevops-demo-agent       AgentCore / Bedrock / runtime tool access
     redevops-demo-readonly    monitoring · Cost Explorer · CloudWatch · Inspector · Config · Macie reads
   ```
   Sidekick reads the bootstrap key, **assumes the least-privilege role per task via STS** (short-lived session), and never holds two long-lived key sets.

3. **Compliance/Privacy = real end-to-end against seeded conditions** (real path, planted findings — *not* hard-coded output). Real cluster/config/security + OpenSCAP/CIS checks against a controlled target; **one intentionally-vulnerable workload/config**; a **tiny synthetic S3 dataset with clearly-fake PII**; the real operators *discover* those known conditions; Macie used where scan timing is predictable. Seed the account **before** recording; cache only prior scan state if cloud timing demands it, clearly labelled. Reproducible run, credible integration.

4. **Format: interactive chaptered walkthrough is canonical; publish one 4–5 min edit + three short clips.**
   - Chapters: *One prompt · Architecture & cost · Approval & deployment · Image scan & hardening · Monitoring armed · Fault detected · Diagnosis & remediation · New application added.*
   - Short clips (reusable for LinkedIn/homepage): (a) deploy from one prompt · (b) detect & heal an induced fault · (c) add the outreach app to the running platform.

## 8. "Done" for the public demo
From one sentence in Projects: recommend architecture + cost → **approve** → provision real EKS + Helm monitoring → deploy security spine → **scan ECR → harden → rollout** → arm monitoring → **catch an induced fault, diagnose with evidence, propose with cost, approve, remediate, re-verify** → compliance + privacy posture visible → then **accept a brand-new user goal (deploy the outreach demo for real)** onto the running, monitored, self-healing stack — every step gated, inspectable, replayable, with Context Runtime choosing the intelligence path and Mission Runtime governing all of it.
