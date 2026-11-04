   output "cluster_name" {
     value = module.eks.cluster_name
   }

   output "region" {
     value = var.region
   }

   output "configure_kubectl" {
     description = "Run this to point kubectl at the new cluster"
     value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
   }

   output "vpc_id" {
  value = module.vpc.vpc_id
}