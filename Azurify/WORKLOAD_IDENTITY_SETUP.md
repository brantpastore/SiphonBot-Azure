Workload Identity / OIDC setup for GitHub Actions (exact commands)

Run these commands in Azure CLI (logged in as a user with sufficient privileges).

Change the placeholder values (RESOURCE_GROUP, SUBSCRIPTION_ID, GITHUB_OWNER, GITHUB_REPO, and BRANCH) before running.

1) Variables

```bash
RESOURCE_GROUP="my-resource-group"
LOCATION="eastus"
SUBSCRIPTION_ID="<your-subscription-id>"
GITHUB_OWNER="<github-owner>"
GITHUB_REPO="<github-repo>"
BRANCH="refs/heads/main"
APP_NAME="siphonbot-github-actions"
FED_NAME="github-actions-siphonbot"
```

2) Create an App Registration

```bash
app=$(az ad app create --display-name "$APP_NAME" --query "{appId:appId,id:id}" -o json)
APP_ID=$(echo "$app" | jq -r .appId)
APP_OBJECT_ID=$(echo "$app" | jq -r .id)
echo "Created app: appId=$APP_ID objectId=$APP_OBJECT_ID"
```

3) Create a Service Principal for role assignments

```bash
az ad sp create --id $APP_ID
SP_OBJECT_ID=$(az ad sp show --id $APP_ID --query objectId -o tsv)
echo "Created service principal: objectId=$SP_OBJECT_ID"
```

4) Assign the required role(s) to the service principal (scope example: resource group)

Grant Contributor on the resource group (or choose narrower roles like "AcrPush", "Storage Blob Data Contributor", etc.):

```bash
az role assignment create --assignee-object-id $SP_OBJECT_ID --role "Contributor" --scope /subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP
```

5) Create the federated credential (GitHub OIDC)

This registers a federated identity credential on the application so GitHub Actions can request tokens.

```bash
PAYLOAD=$(cat <<EOF
{
  "name": "$FED_NAME",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:$GITHUB_OWNER/$GITHUB_REPO:ref:$BRANCH",
  "description": "GitHub Actions OIDC federation for $GITHUB_OWNER/$GITHUB_REPO",
  "audiences": [ "api://AzureADTokenExchange" ]
}
EOF
)

az rest --method POST \
  --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID/federatedIdentityCredentials" \
  --headers "Content-Type=application/json" \
  --body "$PAYLOAD"

echo "Created federated credential $FED_NAME for app $APP_ID"
```

Notes:
- The `az rest` call requires your Azure CLI login to have permission to call Microsoft Graph (you'll typically be an owner/admin).
- The `subject` field supports more granular patterns — e.g. `repo:OWNER/REPO:ref:refs/heads/BRANCH` or `environment:OWNER/REPO:environment:ENV_NAME`.

6) Add repository secrets (use the GitHub CLI or GitHub UI)

If you have the `gh` CLI authenticated and permission, run:

```bash
gh secret set AZURE_CLIENT_ID --body "$APP_ID" --repo $GITHUB_OWNER/$GITHUB_REPO
gh secret set AZURE_TENANT_ID --body "$(az account show --query tenantId -o tsv)" --repo $GITHUB_OWNER/$GITHUB_REPO
gh secret set AZURE_SUBSCRIPTION_ID --body "$SUBSCRIPTION_ID" --repo $GITHUB_OWNER/$GITHUB_REPO
```

Alternatively, set these secrets in GitHub Repository Settings → Secrets and variables → Actions.

7) (Optional) If you still want to support service principal fallback, create `AZURE_CREDENTIALS` JSON and add as secret:

```bash
sp=$(az ad sp create-for-rbac --name "http://$APP_NAME-sp" --role contributor --scopes /subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP --sdk-auth)
# Add to GitHub repo secrets (copy-paste or use gh)
echo "$sp" | gh secret set AZURE_CREDENTIALS --repo $GITHUB_OWNER/$GITHUB_REPO
```

8) Validate

In your GitHub Actions workflow, ensure `azure/login` runs with `enable-oidc: true` (the `ci-cd-full.yml` already prefers OIDC). When workflow runs, the job should be able to authenticate without `AZURE_CREDENTIALS` if OIDC is configured correctly.

If you want, I can generate a ready-to-run script that fills in your repo values automatically.
