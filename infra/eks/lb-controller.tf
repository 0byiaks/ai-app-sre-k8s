# 0. Fetch AWS's official permission list for the controller straight from GitHub.
data "http" "lb_controller_policy" {
  url = "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/main/docs/install/iam_policy.json"
}

# 1. The badge: only pods (through Pod Identity) can use this role.
resource "aws_iam_role" "lb_controller" {
  name = "${var.name}-lb-controller"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

# 2. What the badge allows: the permission list fetched above.
resource "aws_iam_policy" "lb_controller" {
  name   = "${var.name}-lb-controller"
  policy = data.http.lb_controller_policy.response_body
}

resource "aws_iam_role_policy_attachment" "lb_controller" {
  role       = aws_iam_role.lb_controller.name
  policy_arn = aws_iam_policy.lb_controller.arn
}

# 3. Who wears the badge: the controller's pod in kube-system.
resource "aws_eks_pod_identity_association" "lb_controller" {
  cluster_name    = module.eks.cluster_name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  role_arn        = aws_iam_role.lb_controller.arn
}