variable "region" {
  description = "Demo region (Bedrock + AgentCore availability)."
  type        = string
  default     = "us-east-1"
}

variable "bootstrap_principal_arn" {
  description = <<-EOT
    ARN of the bootstrap IAM user/principal whose access key lives in Vault at
    secret/redevops/aws-demo/bootstrap. It is the ONLY principal trusted to assume the
    three demo roles. Create this user with a single permission: sts:AssumeRole on the
    three role ARNs (nothing else).
  EOT
  type        = string
}

variable "monthly_budget_usd" {
  description = "Account-level hard cap. At 100% the emergency-destroy path fires."
  type        = number
  default     = 100
}

variable "budget_notify_emails" {
  description = "Emails alerted at 80% and 100% of the cap."
  type        = list(string)
  default     = []
}

variable "repo_url" {
  description = "Git URL of this repo; CodeBuild clones it to run the out-of-band terraform destroy."
  type        = string
  default     = "https://github.com/redevops-io/redevops-demo.git"
}

variable "eks_env_path" {
  description = "Path within the repo to the env whose destroy the kill-switch runs."
  type        = string
  default     = "infra/terraform/envs/aws"
}
