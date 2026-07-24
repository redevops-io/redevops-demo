data "digitalocean_kubernetes_versions" "available" {}

locals {
  name = "${var.project_name}-${var.environment}"
  # DO tags are a flat list of strings (no key/value). project/env/ttl that can't be tags ride in names.
  tags               = ["demo", "redevops-demo", "env-${var.environment}"]
  kubernetes_version = var.kubernetes_version != "" ? var.kubernetes_version : data.digitalocean_kubernetes_versions.available.latest_version
}

# ---- VPC (private network for the cluster) ----
# digitalocean_vpc has no tags field; project/env/ttl ride in name + description.
resource "digitalocean_vpc" "demo" {
  name        = "${local.name}-vpc"
  region      = var.region
  ip_range    = var.vpc_cidr
  description = "redevops-demo ${var.environment} (ttl ${var.ttl})"
}

# ---- DOKS (one small autoscaling node pool) ----
resource "digitalocean_kubernetes_cluster" "demo" {
  name     = local.name
  region   = var.region
  version  = local.kubernetes_version
  vpc_uuid = digitalocean_vpc.demo.id

  # demo: don't let DO auto-bump the control plane under us
  auto_upgrade                     = false
  ha                               = false
  tags                             = local.tags
  destroy_all_associated_resources = true # demo: let `terraform destroy` clean LBs/volumes too

  node_pool {
    name       = "demo-nodes"
    size       = var.node_size
    node_count = var.node_desired_size
    auto_scale = true
    min_nodes  = var.node_min_size
    max_nodes  = var.node_max_size
    tags       = local.tags
    labels     = { role = "demo" }
  }
}

# ---- DigitalOcean Container Registry (account-global; exactly one per account) ----
# The AWS env creates N ECR repos; on DO there is a single registry and the app image repos live
# *inside* it (created implicitly on first push). We create the registry here; the repo names are
# reflected via var.app_repos and the outputs — NOT as separate registry resources.
# digitalocean_container_registry has no tags field.
resource "digitalocean_container_registry" "demo" {
  name                   = var.registry_name
  subscription_tier_slug = var.registry_subscription_tier
  region                 = var.region
}
