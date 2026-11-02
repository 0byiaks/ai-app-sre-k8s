output "ecr_urls" {
  value = { for name, repo in aws_ecr_repository.this : name => repo.repository_url }
}

output "registry" {
  description = "Use this to log Docker in to ECR"
  value       = split("/", aws_ecr_repository.this["relay-backend"].repository_url)[0]
}