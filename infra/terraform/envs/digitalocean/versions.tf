# The demo DOKS env the Mission Runtime's infra operator drives (terraform plan/apply/destroy).
# DigitalOcean mirror of the AWS/EKS env: deliberately small + cheap — one autoscaling node pool,
# a single VPC, one starter/basic Container Registry, demo-tagged where DO allows tags, TTL in names.
terraform {
  required_version = ">= 1.6"
  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.0"
    }
  }
}

provider "digitalocean" {
  # DO auth is a single account-wide API token (no role assumption / STS analog).
  # Empty default lets `terraform validate` run without a real token; supply -var do_token=... to plan/apply.
  token = var.do_token
}
