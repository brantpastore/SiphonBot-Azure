#!/usr/bin/env bash
set -euo pipefail

# Fix role assignment for the service principal created during OIDC setup.
# Usage: RESOURCE_GROUP=... SUBSCRIPTION_ID=... SP_OBJECT_ID=... ./fix_role_assignment.sh

if [ -z "${RESOURCE_GROUP:-}" ] || [ -z "${SUBSCRIPTION_ID:-}" ]; then
  echo "Usage: RESOURCE_GROUP=... SUBSCRIPTION_ID=... (SP_OBJECT_ID=... | APP_ID=...) $0"
  exit 2
fi

# Prefer explicit service principal object id, otherwise derive from app/client id
if [ -n "${SP_OBJECT_ID:-}" ]; then
  TARGET_SP_OBJECT_ID="$SP_OBJECT_ID"
elif [ -n "${APP_ID:-}" ]; then
  echo "Deriving service principal object id from appId $APP_ID"
  TARGET_SP_OBJECT_ID=$(az ad sp show --id "$APP_ID" --query objectId -o tsv || true)
  if [ -z "$TARGET_SP_OBJECT_ID" ]; then
    echo "Failed to find service principal for appId $APP_ID. Ensure the service principal exists (az ad sp create --id $APP_ID)."
    exit 3
  fi
else
  echo "Either SP_OBJECT_ID or APP_ID must be provided." >&2
  exit 2
fi

echo "Assigning Contributor role to service principal (objectId=$TARGET_SP_OBJECT_ID) on resource group $RESOURCE_GROUP"
az role assignment create \
  --assignee-object-id "$TARGET_SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role Contributor \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP"

echo "Role assignment complete. Consider narrowing the role to AcrPush for ACR push-only permissions."
