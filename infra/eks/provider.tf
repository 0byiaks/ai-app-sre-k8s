terraform {
  required_version = ">= 1.10"

  backend "s3" {
    bucket         = "relay-tfstate-716769866080"
    key            = "infra/eks/terraform.tfstate"
    region         = "eu-west-2"
    use_lockfile     = true
    encrypt        = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  # Every resource gets these tags, so you can find (and cost) everything this project creates.
  default_tags {
    tags = {
      Project   = "relay-ai-sre"
      ManagedBy = "terraform"
    }
  }
}

