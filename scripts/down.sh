#!/usr/bin/env bash
# Tear the EKS environment down to stop the bill. Keeps infra/shared (ECR images, secrets).
#
# Usage (from anywhere in the repo):  ./scripts/down.sh
set -euo pipefail

REGION="eu-west-2"
CLUSTER="relay"
cd "$(dirname "$0")/.."

step() { printf '\n\033[1;31m==> %s\033[0m\n' "$1"; }

read -r -p "Destroy the EKS cluster '$CLUSTER'? Type yes: " answer
[ "$answer" = "yes" ] || { echo "Cancelled."; exit 0; }

if aws eks describe-cluster --region "$REGION" --name "$CLUSTER" > /dev/null 2>&1; then
  aws eks update-kubeconfig --region "$REGION" --name "$CLUSTER" > /dev/null

  step "1/2 Deleting disks first (so no EBS volumes are left behind)"
  helm uninstall loki -n monitoring --wait 2> /dev/null || true
  kubectl delete pvc --all -n monitoring --wait=false
  for _ in $(seq 1 30); do   # wait up to ~2.5 minutes for the EBS volumes to go
    [ -z "$(kubectl get pv -o name)" ] && break
    sleep 5
  done
  kubectl get pv -o name | grep -q . && echo "Warning: some volumes still exist; check EC2 -> Volumes after."
else
  echo "Cluster is not running; skipping the Kubernetes clean-up."
fi

step "2/2 Destroying the cluster (Terraform, ~10-15 min)"
terraform -chdir=infra/eks destroy -auto-approve -input=false

echo
echo "Done. ECR and Secrets Manager (infra/shared) are kept, at under \$1 a month."