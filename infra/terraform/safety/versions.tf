# Account-level SAFETY + IAM bootstrap for the ReDevOps AWS demo.
# Applied ONCE by the account owner in the dedicated demo account (us-east-1).
# Everything here lives OUTSIDE Mission Runtime / Sidekick / EKS so it survives an
# unhealthy stack: a Budgets alarm -> SNS -> Lambda -> CodeBuild `terraform destroy`,
# plus the three least-privilege roles Sidekick assumes.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      demo    = "aws"
      project = "redevops-demo"
      layer   = "safety"
    }
  }
}
