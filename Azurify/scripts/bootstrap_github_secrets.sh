#!/usr/bin/env bash
set -euo pipefail

# Bootstrap GitHub repository secrets needed by the CI workflow using `gh`.
# Usage: GITHUB_OWNER=... GITHUB_REPO=... ACR_NAME=... RESOURCE_GROUP=... CONTAINERAPP_NAME=... IMAGE_NAME=... ./bootstrap_github_secrets.sh

if [ -z "${GITHUB_OWNER:-}" ] || [ -z "${GITHUB_REPO:-}" ]; then
  echo "Usage: GITHUB_OWNER=... GITHUB_REPO=... ACR_NAME=... RESOURCE_GROUP=... CONTAINERAPP_NAME=... IMAGE_NAME=... $0"
  exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI not found. Install and authenticate 'gh' before running this script."
  exit 3
fi

echo "Setting AZURE_CLIENT_ID (appId)"
if [ -z "${AZURE_CLIENT_ID:-}" ]; then
  echo "Please set environment variable AZURE_CLIENT_ID to the App ID from Azure AD." 
  exit 4
fi

echo "Setting required repo secrets for $GITHUB_OWNER/$GITHUB_REPO"
gh secret set AZURE_CLIENT_ID --body "$AZURE_CLIENT_ID" --repo "$GITHUB_OWNER/$GITHUB_REPO"
gh secret set AZURE_TENANT_ID --body "$(az account show --query tenantId -o tsv)" --repo "$GITHUB_OWNER/$GITHUB_REPO"
gh secret set AZURE_SUBSCRIPTION_ID --body "$(az account show --query id -o tsv)" --repo "$GITHUB_OWNER/$GITHUB_REPO"

if [ -n "${ACR_NAME:-}" ]; then
  gh secret set ACR_NAME --body "$ACR_NAME" --repo "$GITHUB_OWNER/$GITHUB_REPO"
fi
if [ -n "${RESOURCE_GROUP:-}" ]; then
  gh secret set RESOURCE_GROUP --body "$RESOURCE_GROUP" --repo "$GITHUB_OWNER/$GITHUB_REPO"
fi
if [ -n "${CONTAINERAPP_NAME:-}" ]; then
  gh secret set CONTAINERAPP_NAME --body "$CONTAINERAPP_NAME" --repo "$GITHUB_OWNER/$GITHUB_REPO"
fi
if [ -n "${IMAGE_NAME:-}" ]; then
  gh secret set IMAGE_NAME --body "$IMAGE_NAME" --repo "$GITHUB_OWNER/$GITHUB_REPO"
fi

echo "Secrets set. Review them in the GitHub repository settings."
