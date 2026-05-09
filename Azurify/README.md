Azurify — Azure scaffolding for SiphonBot

This folder contains a minimal Option A scaffold: Bicep IaC, a GitHub Actions CI/CD workflow, a local deploy helper, and a sample Container Apps manifest.

Files added:
- Azurify/infra/main.bicep — Bicep skeleton to create ACR, Storage, Log Analytics, App Insights, and Container Apps environment.
- Azurify/infra/parameters.json — example parameter values.
- Azurify/github/workflows/ci-cd.yml — GitHub Actions workflow to build, push, and deploy.
- Azurify/deploy/azure-deploy.sh — helper script to run local deployment with Azure CLI.
- Azurify/containerapp/containerapp.job.yaml — example container app job spec (template).

Quickstart

1. Create GitHub secrets:
   - `AZURE_CREDENTIALS` (service principal JSON)
   - `ACR_NAME` (your ACR name)
   - `RESOURCE_GROUP` (target RG)
   - `CONTAINERAPP_NAME` (container app name)
   - `IMAGE_NAME` (image repository name, e.g. siphonbot)

2. To deploy locally with the Azure CLI:
```bash
bash Azurify/deploy/azure-deploy.sh <RESOURCE_GROUP> <LOCATION> <ACR_NAME>
```

3. CI/CD is provided in `Azurify/github/workflows/ci-cd.yml` (push to `main`).

Customize the Bicep parameters in `Azurify/infra/parameters.json` before using.

---
Produced by GitHub Copilot assistant — adjust names and secrets before running.

Workload identity (recommended)

- Prefer Workload Identity Federation (OIDC) for GitHub Actions to avoid long-lived service principals.
- To enable:
   1. Create an Azure AD app registration and add a federated credential for your repo/branch (GitHub OIDC). See: https://learn.microsoft.com/azure/active-directory/develop/workload-identity-federation
   2. Add these repository secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.
   3. In GitHub Actions, the workflow will prefer OIDC if these are set; otherwise it falls back to `AZURE_CREDENTIALS`.

Example (high level):

```bash
# Create an app registration
az ad app create --display-name "siphonbot-github" --output json
# Create a service principal for role assignments (if needed)
az ad sp create --id <appId>
# Add a federated credential (requires Microsoft Graph or az rest calls) — see MS docs for exact command
```

If you want, I can generate the exact `az` commands and a sample `az rest` call to create the federated credential for your repository.
