#!/usr/bin/env bash
set -euo pipefail

# Validate API versions used in a Bicep file against the subscription/provider for a location.
# Usage: ./validate_bicep_api_versions.sh <bicep-file> <location>

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <bicep-file> <location>"
  exit 1
fi

BICEP_FILE=$1
LOCATION=$2

command -v az >/dev/null 2>&1 || { echo "az CLI required" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq required" >&2; exit 1; }

echo "Scanning $BICEP_FILE for resource declarations..."

matches=$(grep -Po "resource\s+\S+\s+'\K[^']+@[^']+(?=')" "$BICEP_FILE" || true)
if [ -z "$matches" ]; then
  echo "No resources with inline api-versions found in $BICEP_FILE"
  exit 0
fi

echo "$matches" | while IFS= read -r entry; do
  # entry example: Microsoft.Web/managedEnvironments@2023-01-01
  ns_type=$(echo "$entry" | cut -d@ -f1)
  api_ver=$(echo "$entry" | cut -d@ -f2)
  namespace=$(echo "$ns_type" | cut -d/ -f1)
  resourceType=$(echo "$ns_type" | cut -d/ -f2-)

  echo "Checking $namespace/$resourceType @ $api_ver for location $LOCATION"

  prov_json=$(az provider show -n "$namespace" -o json 2>/dev/null || true)
  if [ -z "$prov_json" ]; then
    echo "  ERROR: Provider $namespace not found or not registered in this subscription." >&2
    continue
  fi

  # Find matching resourceType entry (resourceType property may vary; try both)
  apiVersions=$(echo "$prov_json" | jq -r --arg rt "$resourceType" '.resourceTypes[] | select(.resourceType==($rt) or .name==($rt)) | .apiVersions[]' 2>/dev/null || true)
  if [ -z "$apiVersions" ]; then
    echo "  WARNING: Resource type $resourceType not listed under provider $namespace." >&2
    echo "    Provider resourceTypes keys:"
    echo "$prov_json" | jq -r '.resourceTypes[].resourceType' | sed 's/^/      - /'
    continue
  fi

  # Check api version availability
  ok=false
  while IFS= read -r v; do
    if [ "$v" = "$api_ver" ]; then
      ok=true
      break
    fi
  done <<< "$apiVersions"

  if [ "$ok" = true ]; then
    # check locations support
    locations=$(echo "$prov_json" | jq -r --arg rt "$resourceType" '.resourceTypes[] | select(.resourceType==($rt) or .name==($rt)) | .locations[]' 2>/dev/null || true)
    if echo "$locations" | grep -qi "^$LOCATION$"; then
      echo "  OK: apiVersion $api_ver supported in $LOCATION"
    else
      echo "  WARNING: apiVersion $api_ver exists for $namespace/$resourceType but not listed for location $LOCATION" >&2
      echo "    Available locations:"
      echo "$locations" | sed 's/^/      - /'
    fi
  else
    echo "  ERROR: apiVersion $api_ver not available for $namespace/$resourceType" >&2
    echo "    Supported apiVersions:"
    echo "$apiVersions" | sed 's/^/      - /'
  fi

done
