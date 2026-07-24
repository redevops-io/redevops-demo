# --- Auth (DO's model: one account-wide API token; no per-role STS analog) ---
variable "do_token" {
  description = "DigitalOcean API token (read/write). Sensitive — never commit. Placeholder default lets `terraform validate` run without a real token; pass -var do_token=... to plan/apply."
  type        = string
  sensitive   = true
  default     = ""
}

variable "region" {
  type    = string
  default = "nyc3"
}

variable "project_name" {
  type    = string
  default = "redevops-demo"
}

variable "environment" {
  type    = string
  default = "demo"
}

variable "ttl" {
  description = "Informational teardown hint for the budget guard / operators. DO tags can't be key/value, so this rides in resource names/descriptions."
  type        = string
  default     = "24h"
}

# DOKS version slugs look like "1.31.1-do.0" and rotate out fast. Empty => use the latest the
# provider reports (recommended). Set an explicit slug only to pin; `terraform validate` doesn't
# reach the API, so a wrong slug only surfaces at plan/apply.
variable "kubernetes_version" {
  type    = string
  default = ""
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

# Small + cheap for a demo. s-2vcpu-4gb ≈ the t3.large the AWS env uses.
variable "node_size" {
  type    = string
  default = "s-2vcpu-4gb"
}

variable "node_min_size" {
  type    = number
  default = 2
}

variable "node_max_size" {
  type    = number
  default = 3
}

# Initial pool size (mirrors the AWS desired=2). Autoscaling floats it between min/max.
variable "node_desired_size" {
  type    = number
  default = 2
}

# NOTE: DigitalOcean has no spot/preemptible analog, so there is no `use_spot` here.

# DOCR is account-global (one registry per account). Its name must be globally unique across all of
# DO (like an S3 bucket name) — override for your account. The AWS ECR *repo list* maps to image
# repos *inside* this single registry, surfaced via var.app_repos / the outputs (not separate registries).
variable "registry_name" {
  type    = string
  default = "redevops-demo"
}

# starter = 1 repo / 500MB (too small for the 5 demo apps); basic = unlimited repos / 5GB.
variable "registry_subscription_tier" {
  type    = string
  default = "basic"
}

# Image repo names created (on push) inside the single DOCR — the DO analog of the AWS ecr_repos list.
variable "app_repos" {
  type    = list(string)
  default = ["outreach-engine", "edge-sentinel", "agentic-compliance", "agentic-privacy", "induced-fault-app"]
}
