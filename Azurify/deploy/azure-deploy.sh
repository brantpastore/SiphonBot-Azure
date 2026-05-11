#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: $0 <RESOURCE_GROUP> <LOCATION> <ACR_NAME>"
  exit 1
fi

RG=$1
LOCATION=$2
ACR_NAME=$3

echo "Creating resource group $RG in $LOCATION"
az group create -n "$RG" -l "$LOCATION"

# Ensure Microsoft.App provider is registered (required for Container Apps)
echo "Registering provider Microsoft.App (if needed)"
az provider show -n Microsoft.App >/dev/null 2>&1 || true
az provider register -n Microsoft.App >/dev/null
echo "Waiting for Microsoft.App registration to complete..."
for i in {1..30}; do
  state=$(az provider show -n Microsoft.App -o tsv --query registrationState)
  if [ "$state" = "Registered" ]; then
    echo "Microsoft.App provider registered."
    break
  fi
  echo "  registrationState=$state; retrying..."
  sleep 3
done

# Validate/normalize ACR name: must be 5-50 chars, lower-case, alphanumeric and dashes; no underscores
norm_acr=$(echo "$ACR_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/_/-/g')
norm_acr=$(echo "$norm_acr" | sed 's/[^a-z0-9-]//g')
if [ ${#norm_acr} -lt 5 ] || [ ${#norm_acr} -gt 50 ]; then
  echo "Normalized ACR name '$norm_acr' must be 5-50 characters; please choose a different name." >&2
  exit 1
fi
if [[ "$norm_acr" =~ -- ]]; then
  # collapse multiple dashes
  norm_acr=$(echo "$norm_acr" | sed 's/\-\-\+/-/g')
fi
if [ "$norm_acr" != "$ACR_NAME" ]; then
  echo "ACR name normalized from '$ACR_NAME' to '$norm_acr'"
fi
ACR_NAME="$norm_acr"

echo "Deploying Bicep template"
az deployment group create \
  --resource-group "$RG" \
  --template-file "$(dirname "$0")/../infra/main.bicep" \
  --parameters location="$LOCATION" acrName="$ACR_NAME"

echo "Done. Review outputs with: az deployment group show -g $RG --name $(az deployment group list -g $RG --query "[0].name" -o tsv)"
