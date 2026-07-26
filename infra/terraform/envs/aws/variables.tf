variable "region" {
  type    = string
  default = "us-east-1"
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

variable "kubernetes_version" {
  type = string
  # 1.35: AWS is retiring 1.33, so pin ahead to keep runway before 1.34 also expires.
  default = "1.35"
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

# Small + cheap for a demo. kube-prometheus-stack + Loki + a few operators fit on 2x t3.large.
variable "node_instance_types" {
  type    = list(string)
  default = ["t3.large"]
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
  description = "Spot capacity for the demo node group (cheaper; fine for a demo)."
  type        = bool
  default     = true
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach the public EKS API endpoint. Tighten for real runs."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# ECR repos created for the demo workloads (edge-sentinel scans these).
variable "ecr_repos" {
  type    = list(string)
  default = ["outreach-engine", "edge-sentinel", "agentic-compliance", "agentic-privacy", "induced-fault-app"]
}
