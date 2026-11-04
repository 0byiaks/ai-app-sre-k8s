#!/usr/bin/env bash
# Bring the whole EKS environment up: cluster, platform, monitoring, and the apps.
# Safe to run again: every step checks or updates instead of failing if it already exists.
#
# Usage (from anywhere in the repo):  ./scripts/up.sh
set -euo pipefail

REGION="eu-west-2"
CLUSTER="relay"
cd "$(dirname "$0")/.."   # always run from the repo root

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }

step "1/9 Checking tools and AWS login"
for tool in aws terraform kubectl helm git; do
  command -v "$tool" > /dev/null || { echo "Missing tool: $tool"; exit 1; }
done
aws sts get-caller-identity --query Account --output text > /dev/null \
  || { echo "Not logged in to AWS. Log in first (aws login / aws sso login)."; exit 1; }

step "2/9 Cluster (Terraform, ~15 min the first time)"
terraform -chdir=infra/eks init -input=false > /dev/null
terraform -chdir=infra/eks apply -auto-approve -input=false

step "3/9 Connecting kubectl"
aws eks update-kubeconfig --region "$REGION" --name "$CLUSTER"
kubectl wait --for=condition=Ready nodes --all --timeout=300s

step "4/9 Namespaces and CI permissions"
for ns in relay aiops monitoring; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
done
kubectl apply -f platform/rbac/ci-deployer.yaml

step "5/9 External Secrets (AWS Secrets Manager -> Kubernetes Secrets)"
helm repo add external-secrets https://charts.external-secrets.io --force-update > /dev/null
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update > /dev/null
helm repo add grafana https://grafana.github.io/helm-charts --force-update > /dev/null
helm repo update > /dev/null
helm upgrade --install external-secrets external-secrets/external-secrets \
  -n external-secrets --create-namespace --wait
kubectl wait --for=condition=Established crd/clustersecretstores.external-secrets.io --timeout=60s
kubectl apply -f platform/external-secrets/cluster-secret-store.yaml
kubectl apply -f platform/external-secrets/secrets.yaml
for ns in relay aiops monitoring; do
  kubectl wait --for=condition=Ready externalsecret --all -n "$ns" --timeout=120s
done

step "6/9 Default storage (gp3)"
kubectl apply -f platform/storage/gp3.yaml

step "6b/9 AWS Load Balancer Controller (builds the public ALB)"
helm repo add eks https://aws.github.io/eks-charts --force-update > /dev/null
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller -n kube-system \
  --set clusterName="$CLUSTER" \
  --set region="$REGION" \
  --set vpcId="$(terraform -chdir=infra/eks output -raw vpc_id)" \
  --set serviceAccount.name=aws-load-balancer-controller \
  --wait

step "7/9 Monitoring: Prometheus, Grafana, Alertmanager, Loki, Alloy"
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack -n monitoring \
  -f monitoring/values.yaml -f monitoring/values-eks.yaml --wait --timeout 10m
helm upgrade --install loki grafana-community/loki -n monitoring \
  -f monitoring/loki-values.yaml
helm upgrade --install alloy grafana/alloy -n monitoring \
  -f monitoring/alloy-values.yaml --wait

step "8/9 Apps: deploying the latest main build from ECR"
git fetch --quiet origin main
TAG=$(git rev-parse origin/main)
if aws ecr describe-images --region "$REGION" --repository-name relay-backend \
     --image-ids imageTag="$TAG" > /dev/null 2>&1; then
  helm upgrade --install relay helm/relay -n relay -f helm/relay/values-eks.yaml \
    --set backend.image.tag="$TAG" --set frontend.image.tag="$TAG" \
    --force-conflicts --wait --timeout 5m
  helm upgrade --install sre-agent helm/sre-agent -n aiops -f helm/sre-agent/values-eks.yaml \
    --set image.tag="$TAG" --force-conflicts --wait --timeout 5m
else
  echo "No image for $TAG in ECR yet (CI may still be building). Push to main and CI will deploy."
fi

step "9/9 Done"
kubectl get pods -n relay
kubectl get pods -n aiops
cat <<MSG

Open Relay:    kubectl port-forward svc/frontend -n relay 8080:80     -> http://localhost:8080
Open Grafana:  kubectl port-forward svc/monitoring-grafana -n monitoring 3000:80 -> http://localhost:3000

The cluster costs money while it runs. When you're done:  ./scripts/down.sh
MSG