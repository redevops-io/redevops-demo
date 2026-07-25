# Required: the GCP project the demo lives in. Placeholder default lets `terraform validate`
# run without real creds; override with -var 'gcp_project=...' or TF_VAR_gcp_project for a real apply.
variable "gcp_project" {
  description = "GCP project id to deploy the demo into."
  type        = string
  default     = "redevops-demo-project"
}

variable "region" {
  type    = string
  default = "us-central1"
}

# Zonal cluster: one zone in var.region keeps the control plane single-replica (free-tier friendly)
# and avoids cross-AZ node spend. Mirrors the AWS env's "cheap demo" spirit.
variable "zone" {
  type    = string
  default = "us-central1-a"
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
  description = "Informational teardown hint for the budget guard / operators."
  type        = string
  default     = "24h"
}

# GKE tracks a release channel rather than a pinned control-plane version by default.
# REGULAR balances freshness + stability; the AWS env's kubernetes_version analog.
variable "release_channel" {
  description = "GKE release channel: RAPID | REGULAR | STABLE | UNSPECIFIED."
  type        = string
  default     = "REGULAR"
}

variable "subnet_cidr" {
  type    = string
  default = "10.42.0.0/20"
}

# Secondary ranges for a VPC-native (alias-IP) cluster — pods + services.
variable "pods_cidr" {
  type    = string
  default = "10.44.0.0/14"
}

variable "services_cidr" {
  type    = string
  default = "10.48.0.0/20"
}

# Small + cheap for a demo. kube-prometheus-stack + Loki + a few operators fit on 2x e2-standard-2.
variable "node_machine_type" {
  type    = string
  default = "e2-standard-2"
}

variable "node_min_size" {
  type    = number
  default = 2
}

variable "node_max_size" {
  type    = number
  default = 3
}

variable "node_desired_size" {
  type    = number
  default = 2
}

variable "node_disk_size" {
  type    = number
  default = 30
}

variable "use_spot" {
  description = "Spot VMs for the demo node pool (cheaper; fine for a demo). GCP analog of AWS SPOT capacity."
  type        = bool
  default     = true
}

# Artifact Registry Docker repos created for the demo workloads (edge-sentinel scans these) —
# the ECR analog. One AR repo is created per name via for_each, mirroring the AWS ecr_repos list.
variable "artifact_repos" {
  type    = list(string)
  default = ["outreach-engine", "edge-sentinel", "agentic-compliance", "agentic-privacy", "induced-fault-app"]
}
