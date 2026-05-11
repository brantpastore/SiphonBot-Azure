#!/usr/bin/env bash
set -euo pipefail

# Automated OIDC setup for GitHub Actions -> Azure AD
# Creates an app registration, service principal, assigns roles, adds a federated credential,
# and optionally writes GitHub repo secrets using `gh`.
#
# Prereqs: az, jq, gh (GitHub CLI)
# Usage: ./setup_oidc.sh --resource-group RG --subscription SUB --owner GITHUB_OWNER --repo GITHUB_REPO [options]

usage() {
  cat <<EOF
Usage: $0 --resource-group RG --subscription SUB --owner OWNER --repo REPO [options]

Options:
  --resource-group RG        Azure resource group to scope role assignments (required)
  --subscription SUB         Azure subscription id (required)
  --owner OWNER              GitHub owner/org (required)
  --repo REPO                GitHub repository name (required)
  --branch BRANCH            Git ref for the federated credential (default: refs/heads/main)
  --app-name NAME            App registration display name (default: siphonbot-github-actions)
  --role ROLE                Role to assign to the service principal on the resource group (default: Contributor)
  --create-az-credentials    Also create AZURE_CREDENTIALS (service principal JSON) and add to repo secrets
  --help

Example:
  $0 --resource-group my-rg --subscription xxxxx --owner myorg --repo myrepo --branch refs/heads/main
EOF
}

RESOURCE_GROUP=""
SUBSCRIPTION_ID=""
GITHUB_OWNER=""
GITHUB_REPO=""
BRANCH="refs/heads/main"
APP_NAME="siphonbot-github-actions"
ROLE_NAME="Contributor"
CREATE_AZ_CREDS=false

VERIFY=false
while [[ $# -gt 0 ]]; do
  case $1 in
    --resource-group) RESOURCE_GROUP="$2"; shift 2;;
    --subscription) SUBSCRIPTION_ID="$2"; shift 2;;
    --owner) GITHUB_OWNER="$2"; shift 2;;
    --repo) GITHUB_REPO="$2"; shift 2;;
    --branch) BRANCH="$2"; shift 2;;
    --app-name) APP_NAME="$2"; shift 2;;
    --role) ROLE_NAME="$2"; shift 2;;
    --create-az-credentials) CREATE_AZ_CREDS=true; shift 1;;
    --verify) VERIFY=true; shift 1;;
    --help) usage; exit 0;;
    *) echo "Unknown arg: $1"; usage; exit 1;;
  esac
done

if [ -z "$RESOURCE_GROUP" ] || [ -z "$SUBSCRIPTION_ID" ] || [ -z "$GITHUB_OWNER" ] || [ -z "$GITHUB_REPO" ]; then
  echo "Missing required arguments." >&2
  usage
  exit 1
fi

command -v az >/dev/null 2>&1 || { echo "az CLI is required" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }
command -v gh >/dev/null 2>&1 || { echo "gh CLI is required to set GitHub secrets (optional)"; }

echo "Using resource group: $RESOURCE_GROUP"
echo "Subscription: $SUBSCRIPTION_ID"
echo "GitHub: $GITHUB_OWNER/$GITHUB_REPO"
echo "Federated subject: repo:$GITHUB_OWNER/$GITHUB_REPO:ref:$BRANCH"

set -x

# 1) Create app registration
app_json=$(az ad app create --display-name "$APP_NAME" --query "{appId:appId,id:id}" -o json)
APP_ID=$(echo "$app_json" | jq -r .appId)
APP_OBJECT_ID=$(echo "$app_json" | jq -r .id)
echo "Created app registration: appId=$APP_ID objectId=$APP_OBJECT_ID"

# 2) Create service principal for role assignments
az ad sp create --id "$APP_ID" >/dev/null
SP_OBJECT_ID=$(az ad sp show --id "$APP_ID" --query objectId -o tsv)
echo "Created service principal objectId=$SP_OBJECT_ID"

# 3) Role assignment for the service principal on the resource group
SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP"
echo "Assigning role '$ROLE_NAME' to service principal on scope $SCOPE"
az role assignment create --assignee-object-id "$SP_OBJECT_ID" --role "$ROLE_NAME" --scope "$SCOPE" || true

# 4) Create federated credential via Microsoft Graph (az rest)
FED_NAME="github-actions-${GITHUB_OWNER}-${GITHUB_REPO}"
PAYLOAD=$(jq -n --arg name "$FED_NAME" --arg issuer "https://token.actions.githubusercontent.com" \
  --arg subject "repo:$GITHUB_OWNER/$GITHUB_REPO:ref:$BRANCH" '{name:$name, issuer:$issuer, subject:$subject, description:"GitHub Actions OIDC federation", audiences:["api://AzureADTokenExchange"]}')

echo "Creating federated credential $FED_NAME on application $APP_OBJECT_ID"
az rest --method POST \
  --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID/federatedIdentityCredentials" \
  --headers "Content-Type=application/json" \
  --body "$PAYLOAD"

echo "Federated credential created. Now setting GitHub secrets (AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID)"

TENANT_ID=$(az account show --query tenantId -o tsv)

if command -v gh >/dev/null 2>&1; then
  gh secret set AZURE_CLIENT_ID --body "$APP_ID" --repo "$GITHUB_OWNER/$GITHUB_REPO"
  gh secret set AZURE_TENANT_ID --body "$TENANT_ID" --repo "$GITHUB_OWNER/$GITHUB_REPO"
  gh secret set AZURE_SUBSCRIPTION_ID --body "$SUBSCRIPTION_ID" --repo "$GITHUB_OWNER/$GITHUB_REPO"
  echo "GitHub repository secrets set (AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID)"
else
  echo "gh CLI not available — please add the following repository secrets manually:"
  echo "  AZURE_CLIENT_ID=$APP_ID"
  echo "  AZURE_TENANT_ID=$TENANT_ID"
  echo "  AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION_ID"
fi

# 5) Optional: create AZURE_CREDENTIALS (service principal JSON) and set as secret
if [ "$CREATE_AZ_CREDS" = true ]; then
  echo "Creating AZURE_CREDENTIALS (service principal JSON) and adding to repo secrets"
  sp_json=$(az ad sp create-for-rbac --name "http://$APP_NAME-sp" --role "$ROLE_NAME" --scopes "$SCOPE" --sdk-auth)
  if command -v gh >/dev/null 2>&1; then
    echo "$sp_json" | gh secret set AZURE_CREDENTIALS --repo "$GITHUB_OWNER/$GITHUB_REPO"
    echo "AZURE_CREDENTIALS set in GitHub repo secrets"
  else
    echo "AZURE_CREDENTIALS (service principal JSON):"
    echo "$sp_json"
    echo "Please add it to repo secrets named AZURE_CREDENTIALS"
  fi
fi

set +x

echo "Done. Validate by running a workflow that uses azure/login with enable-oidc: true." 
echo "If the az rest call fails with permission error, ensure your account has Microsoft Graph permissions to create federated credentials."

if [ "$VERIFY" = true ]; then
  echo "Triggering verification workflow (Verify OIDC) on GitHub..."
  if ! command -v gh >/dev/null 2>&1; then
    echo "gh CLI not available; cannot trigger verification workflow automatically." >&2
    exit 0
  fi

  gh workflow run verify-oidc.yml --repo "$GITHUB_OWNER/$GITHUB_REPO" --ref "$BRANCH" || true

  echo "Waiting for workflow run to start and complete (timeout 180s)..."
  SECONDS_WAITED=0
  MAX_WAIT=180
  SLEEP=5
  while [ $SECONDS_WAITED -lt $MAX_WAIT ]; do
    sleep $SLEEP
    SECONDS_WAITED=$((SECONDS_WAITED + SLEEP))
    # Get latest run for that workflow
    run_info=$(gh run list --workflow verify-oidc.yml --repo "$GITHUB_OWNER/$GITHUB_REPO" --limit 1 --json databaseId,conclusion,status 2>/dev/null || true)
    if [ -z "$run_info" ]; then
      echo "No run yet..."
      continue
    fi
    run_conclusion=$(echo "$run_info" | jq -r '.[0].conclusion')
    run_status=$(echo "$run_info" | jq -r '.[0].status')
    echo "Status: $run_status, conclusion: $run_conclusion"
    if [ "$run_status" = "completed" ]; then
      if [ "$run_conclusion" = "success" ]; then
        echo "Verification workflow succeeded. OIDC setup looks good."
        exit 0
      else
        echo "Verification workflow completed but did not succeed (conclusion=$run_conclusion). Check GitHub Actions run for details." >&2
        exit 2
      fi
    fi
  done

  echo "Timed out waiting for verification workflow (waited ${MAX_WAIT}s). Check the Actions runs in the repository." >&2
  exit 3
fi
