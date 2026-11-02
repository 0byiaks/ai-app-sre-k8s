locals {
  repositories = ["relay-backend", "relay-frontend", "sre-agent"]
}

# One private image repository per app.
resource "aws_ecr_repository" "this" {
  for_each = toset(local.repositories)

  name                 = each.value
  image_tag_mutability = "IMMUTABLE" # a tag can never be overwritten
  force_delete         = true        # lets "terraform destroy" remove it even with images inside

  image_scanning_configuration {
    scan_on_push = true # AWS checks each image for known vulnerabilities
  }
}

# Housekeeping: keep only the newest 10 images in each repository.
resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}