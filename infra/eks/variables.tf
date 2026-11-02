variable "region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-2" # London
}

variable "name" {
  description = "Name prefix for all resources"
  type        = string
  default     = "relay"
}

variable "kubernetes_version" {
  description = "EKS Kubernetes version (use one in standard support)"
  type        = string
  default     = "1.35" # change to the newest number from describe-cluster-versions
}