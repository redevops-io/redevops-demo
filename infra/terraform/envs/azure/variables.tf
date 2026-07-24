# Region. Azure calls it "location"; `region` is kept as an alias output for parity with the AWS env.
variable "location" {
  type    = string
  default = "eastus"
}

variable "subscription_id" {
  description = "Azure subscription id. Empty = take it from ARM_SUBSCRIPTION_ID in the environment."
  type        = string
  default     = ""
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

# AKS control-plane version. Validate does not check availability; `az aks get-versions` at apply time.
variable "kubernetes_version" {
  type    = string
  default = "1.30"
}

variable "vnet_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "node_subnet_cidr" {
  type    = string
  default = "10.42.1.0/24"
}

# Small + cheap for a demo. kube-prometheus-stack + Loki + a few operators fit on 2x Standard_B2s.
variable "node_vm_size" {
  type    = string
  default = "Standard_B2s"
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

# Spot capacity for the demo workloads (cheaper; fine for a demo). NOTE: Azure forbids Spot on the
# system/default node pool, so this gates an ADDITIONAL scale-to-zero Spot *user* node pool rather
# than flipping the system pool — see main.tf. The AWS env uses a single spot-capable node group.
variable "use_spot" {
  description = "Add a scale-to-zero Spot user node pool for demo workloads (Azure spot != AWS spot placement)."
  type        = bool
  default     = true
}

# CIDRs allowed to reach the public AKS API server. Empty = fully public (AWS default is 0.0.0.0/0,
# which Azure rejects as an authorized range; leave empty for the equivalent open-endpoint demo).
variable "authorized_ip_ranges" {
  description = "Authorized IP ranges for the public AKS API server. Empty = open. Tighten for real runs."
  type        = list(string)
  default     = []
}

# ACR (unlike ECR) does not pre-create repositories — they're created on first `docker push`. This
# list is the same demo-workload concept as the AWS env's ecr_repos, surfaced in outputs as the
# push targets edge-sentinel scans (registry.azurecr.io/<repo>).
variable "acr_repos" {
  type    = list(string)
  default = ["outreach-engine", "edge-sentinel", "agentic-compliance", "agentic-privacy", "induced-fault-app"]
}
