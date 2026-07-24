# safety/ — account bootstrap + out-of-band kill-switch

Applied **once** by the account owner in the dedicated us-east-1 demo account. It creates
the three roles Sidekick assumes and the safety net that survives an unhealthy runtime.

## What it creates
- **IAM roles** `redevops-demo-{deployer,agent,readonly}` — trusted only by the bootstrap principal.
- **Budgets alarm** at 80% (email) and 100% (→ SNS → Lambda → CodeBuild `terraform destroy`).
- **Emergency-destroy** CodeBuild project + Lambda — independent of Mission Runtime / Sidekick / EKS.

## Apply
```bash
# 1. create a bootstrap IAM user with ONLY sts:AssumeRole on the three role ARNs; note its ARN.
terraform init
terraform apply \
  -var 'bootstrap_principal_arn=arn:aws:iam::<acct>:user/redevops-demo-bootstrap' \
  -var 'monthly_budget_usd=100' \
  -var 'budget_notify_emails=["you@redevops.io"]' \
  -var 'repo_url=https://github.com/redevops-io/redevops-demo.git'
```

## Then write Vault (privileged token)
```bash
# bootstrap key (the user's access key — created out of band, never echoed):
vault kv put secret/redevops/aws-demo/bootstrap access_key_id=… secret_access_key=…
# config (copy from `terraform output vault_write_hint`):
terraform output -raw vault_write_hint | bash
```

`aws_demo.creds.AwsDemoCreds` then reads `bootstrap` + `config` and assumes the least-privilege
role per task. **No long-lived key beyond the assume-role-only bootstrap user.**
