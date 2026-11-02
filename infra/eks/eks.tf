module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.0"

  name               = var.name
  kubernetes_version = var.kubernetes_version

  # Lets you run kubectl from your laptop. The creator (you) gets admin access.
  endpoint_public_access                   = true
  enable_cluster_creator_admin_permissions = true

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets # nodes live in the private subnets

  # Built-in add-ons managed by AWS.
  addons = {
    vpc-cni = {
      before_compute = true # pod networking, needed before nodes join
    }
    eks-pod-identity-agent = {
      before_compute = true # lets pods get their own AWS permissions
    }
    coredns    = {} # DNS inside the cluster (e.g. "backend" → the backend Service)
    kube-proxy = {} # Service networking on each node
    aws-ebs-csi-driver = {
      # Creates EBS disks for PersistentVolumeClaims (Loki, Prometheus).
      pod_identity_association = [{
        role_arn        = aws_iam_role.ebs_csi.arn
        service_account = "ebs-csi-controller-sa"
      }]
    }
  }

  eks_managed_node_groups = {
    default = {
      # Spot instances: spare AWS capacity at a big discount. Fine for learning;
      # AWS can reclaim them, so production often mixes spot and on-demand.
      capacity_type  = "SPOT"
      instance_types = ["t3.large", "t3a.large", "m5.large"]

      min_size     = 2
      max_size     = 3
      desired_size = 2
    }
  }
}


# IAM role for the EBS disk driver, given to its pod through EKS Pod Identity.
resource "aws_iam_role" "ebs_csi" {
  name = "${var.name}-ebs-csi"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}


data "aws_iam_role" "github_actions" {
  name = "relay-github-actions"
}

# "This IAM role may log in to the cluster."
resource "aws_eks_access_entry" "github_actions" {
  cluster_name  = module.eks.cluster_name
  principal_arn = data.aws_iam_role.github_actions.arn

  kubernetes_groups = ["ci-deployers"]
}

# "...with admin rights, but only in the namespaces CI deploys to."
resource "aws_eks_access_policy_association" "github_actions" {
  cluster_name  = module.eks.cluster_name
  principal_arn = data.aws_iam_role.github_actions.arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSAdminPolicy"

  access_scope {
    type       = "namespace"
    namespaces = ["relay", "aiops"]
  }
  depends_on = [aws_eks_access_entry.github_actions]
}