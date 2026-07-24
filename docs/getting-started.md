# Getting started — from zero to a governed AWS deploy

The hard part isn't running the demo — it's the AWS onboarding. This is the whole path, and
**Sidekick preflights it for you** (`./scripts/doctor.sh`) so you always know the next step.

## What you actually need (that's it)
- **Docker** — the *only* hard local requirement.
- A **browser** and an **AWS account** (with a payment method).

You do **not** need terraform, awscli, ansible, helm, or kubectl installed — they all run **inside
the operator container**. Sidekick shells out to them there, not on your laptop.

## Step 1 — Open an AWS account
1. Sign up at <https://aws.amazon.com> and **add a payment method** (Bedrock invoke is held on
   accounts without one, even under the free-credits program).
2. Set your working region to **us-east-1** (broadest Bedrock/AgentCore availability).
3. *(Optional)* Apply to **AWS Activate / free credits**. Note: new/under-review accounts often
   have **Bedrock invoke restricted** until verified — open a Support case (service: Bedrock) to lift
   it. The demo runs fine without Bedrock in the meantime.

## Step 2 — Create the demo user + permissions
Pick the path that fits you:

**A. Solo / quickstart** — one user, policies attached directly:
1. IAM → Users → create `redevops-demo`.
2. Attach the three policies from `infra/iam/`: `deployer-policy.json`, `readonly-policy.json`,
   `agent-policy.json` (create each as a customer-managed policy, or put them on a group and add the
   user). For Bedrock you may instead attach the managed **`AmazonBedrockFullAccess`**.
3. Create an **access key** for the user.

**B. Governed / team** — bootstrap user + assumed roles (the security boundary):
1. Create a bootstrap user with **only** `sts:AssumeRole` on the three role ARNs.
2. `cd infra/terraform/safety && terraform apply -var bootstrap_principal_arn=…` — this creates the
   `deployer` / `agent` / `readonly` roles **and** the account budget kill-switch.

## Step 3 — Enable Bedrock model access *(optional)*
Bedrock console → **Model access** → enable the models you want (Claude, Nova). IAM permission and
model access are separate; the demo works without either (it uses your existing model plane).

## Step 4 — Give the demo your credentials
- **Solo:** `aws configure` (or `export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=… AWS_REGION=us-east-1`).
  The doctor uses your ambient profile automatically.
- **Governed:** write Vault (never echo the values):
  ```
  vault kv put secret/redevops/aws-demo/bootstrap access_key_id=… secret_access_key=…
  terraform -chdir=infra/terraform/safety output -raw vault_write_hint | bash   # writes .../config
  ```

## Step 5 — Preflight, then bring it up
Sidekick runs its cloud-agnostic **`deployment-preflight`** skill automatically as node 0 of every
deploy mission (shared across AWS/GCP/Azure/DigitalOcean — only the CLI + Terraform syntax differ),
and won't compile a `terraform plan` until it's green. You can also run it by hand:
```bash
./scripts/doctor.sh     # the skill's executable binding: ✓/✗ per requirement + the exact fix
./scripts/up.sh         # Projects cockpit → http://localhost:8080/cockpit
```
When it says **READY**, you're good. Hard blockers are only: **Docker**, **working AWS
credentials**, and **deployer permissions**. Cost Explorer and Bedrock show as *warnings* — nice to
have, not required to deploy.

## Step 6 — Deploy (governed, one prompt)
In Projects: *"Deploy the redevops-demo repo to AWS."* Sidekick inspects the repo, plans the
infrastructure, shows the **cost estimate**, and **pauses at an approval gate**. Approve → it
provisions real EKS + Helm monitoring + the security spine, then asks if it should keep monitoring.

## Safety (always on)
- **Apply `infra/terraform/safety/` before your first real deploy** — it arms an account-level
  Budgets cap + an out-of-band auto-destroy that works even if the runtime is down.
- Tear down any time: the `teardown_app` mission, or `./scripts/emergency-destroy.sh`.
- Real cost while up is ~**$0.20/hr** (small EKS); **$0** torn down.

---
**TL;DR:** install Docker, create an AWS user with the three policies (or `AmazonBedrockFullAccess`
+ deployer/readonly), give the demo your key, run `./scripts/doctor.sh`, then deploy from one prompt.
