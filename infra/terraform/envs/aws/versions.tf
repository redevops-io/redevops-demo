# The demo EKS env the Mission Runtime's infra operator drives (terraform plan/apply/destroy).
# Deliberately small + cheap: one managed node group, single NAT, spot-capable, demo-tagged + TTL.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      demo    = "aws"
      project = "redevops-demo"
      env     = var.environment
      ttl     = var.ttl
    }
  }
}
