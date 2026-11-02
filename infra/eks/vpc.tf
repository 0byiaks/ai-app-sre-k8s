# Use the first 2 availability zones in the region (EKS needs at least 2).
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.0"

  name = "${var.name}-vpc"
  cidr = "10.0.0.0/16"
  azs  = local.azs

  private_subnets = ["10.0.1.0/24", "10.0.2.0/24"]     # worker nodes live here
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24"] # load balancers + NAT gateway

  # Lets nodes in private subnets reach the internet (Claude API, ECR, Slack).
  enable_nat_gateway = true
  single_nat_gateway = true # one NAT instead of one per zone: cheaper, fine for learning

  # Tags that tell Kubernetes which subnets to use for load balancers.
  public_subnet_tags  = { "kubernetes.io/role/elb" = 1 }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = 1 }
}