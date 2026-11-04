locals {
  github_repo = "0byiaks/ai-app-sre-k8s" # owner/repo, must match exactly
  region      = "eu-west-2"
}

data "aws_caller_identity" "current" {}

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}




# The role CI assumes. Only jobs from your repo's main branch can use it.
resource "aws_iam_role" "github_actions" {
  name = "relay-github-actions"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          # The repo uses GitHub's immutable subject (owner@id/repo@id); the plain form is kept as a fallback.
          "token.actions.githubusercontent.com:sub" = [
            "repo:0byiaks@110993470/ai-app-sre-k8s@1435555002:ref:refs/heads/main",
            "repo:${local.github_repo}:ref:refs/heads/main",
          ]
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions" {
  name = "push-images-and-find-cluster"
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrLogin"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*" # this action can't be limited to a repo
      },
      {
        Sid    = "EcrPushToOurRepos"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = [for repo in aws_ecr_repository.this : repo.arn]
      },
      {
        Sid      = "FindTheCluster"
        Effect   = "Allow"
        Action   = "eks:DescribeCluster" # needed by "aws eks update-kubeconfig"
        Resource = "arn:aws:eks:${local.region}:${data.aws_caller_identity.current.account_id}:cluster/relay"
      },
    ]
  })
}

output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}



resource "aws_iam_role" "github_infra" {
  name = "relay-github-infra"
    assume_role_policy = jsonencode({
        Version = "2012-10-17"
        Statement = [{
        Effect    = "Allow"
        Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
        Action    = "sts:AssumeRoleWithWebIdentity"
        Condition = {
            StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:sub" = "repo:${local.github_repo}:ref:refs/heads/main"
            
            }
        }
        }]
    })
}

resource "aws_iam_role_policy_attachment" "github_infra" {
    role = aws_iam_role.github_infra.name
    policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
  
}

output "github_infra_role_arn" {
  value = aws_iam_role.github_infra.arn
}