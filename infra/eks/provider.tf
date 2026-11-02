terraform {
  required_version = ">= 1.6"

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