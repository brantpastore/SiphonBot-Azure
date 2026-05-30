# SiphonBot Secrets Setup Guide

This guide explains how to configure all required secrets for the SiphonBot application on Azure.

## Required Secrets

### Reddit API Credentials

1. **REDDIT_CLIENT_ID** - OAuth client ID
2. **REDDIT_CLIENT_SECRET** - OAuth client secret
3. **REDDIT_USERNAME** - Reddit account username
4. **REDDIT_PASSWORD** - Reddit account password
5. **REDDIT_USER_AGENT** - Custom user agent string (format: `AppName/Version (by RedditUsername)`)

**How to get Reddit credentials:**
1. Go to https://www.reddit.com/prefs/apps
2. Click "Create another app" (or "Create app")
3. Choose "installed app"
4. Copy the client ID and secret
5. Use your Reddit account username and password

### Discord Bot Credentials

1. **DISCORD_TOKEN** - Discord bot token
2. **DISCORD_WEBHOOK_URL** - Webhook URL for posting media

**How to get Discord credentials:**
1. Go to https://discord.com/developers/applications
2. Create a new application
3. Go to "Bot" section and click "Add Bot"
4. Copy the token (this is DISCORD_TOKEN)
5. In your Discord server, create a webhook in a channel
6. Copy the webhook URL (this is DISCORD_WEBHOOK_URL)

## Deployment Options

### Option 1: Bicep Parameters File (Recommended for CI/CD)

Update `Azurify/infra/parameters.json` with your secrets:

```json
{
  "parameters": {
    "redditClientId": { "value": "your_reddit_client_id" },
    "redditClientSecret": { "value": "your_reddit_client_secret" },
    "redditUserAgent": { "value": "SiphonBot/1.0 (by your_username)" },
    "redditUsername": { "value": "your_reddit_username" },
    "redditPassword": { "value": "your_reddit_password" },
    "discordToken": { "value": "your_discord_token" },
    "discordWebhookUrl": { "value": "https://discord.com/api/webhooks/..." }
  }
}
```

⚠️ **IMPORTANT:** Never commit this file to version control if it contains real secrets!

### Option 2: Environment Variables (For Local Testing)

```bash
export REDDIT_CLIENT_ID="your_client_id"
export REDDIT_CLIENT_SECRET="your_client_secret"
export REDDIT_USERNAME="your_username"
export REDDIT_PASSWORD="your_password"
export REDDIT_USER_AGENT="SiphonBot/1.0 (by your_username)"
export DISCORD_TOKEN="your_token"
export WEBHOOK="https://discord.com/api/webhooks/..."
```

Then run locally with Docker:
```bash
docker compose up -d --build
```

### Option 3: Azure Key Vault (After Bicep Deployment)

If you deploy Bicep without secrets, manually add them to Key Vault:

```bash
KEYVAULT_NAME="kvsiphonbot"  # Replace with your actual Key Vault name

az keyvault secret set --vault-name "$KEYVAULT_NAME" \
  --name "siphonbot-reddit-client-id" \
  --value "your_reddit_client_id"

az keyvault secret set --vault-name "$KEYVAULT_NAME" \
  --name "siphonbot-reddit-client-secret" \
  --value "your_reddit_client_secret"

az keyvault secret set --vault-name "$KEYVAULT_NAME" \
  --name "siphonbot-reddit-user-agent" \
  --value "SiphonBot/1.0 (by your_username)"

az keyvault secret set --vault-name "$KEYVAULT_NAME" \
  --name "siphonbot-reddit-username" \
  --value "your_reddit_username"

az keyvault secret set --vault-name "$KEYVAULT_NAME" \
  --name "siphonbot-reddit-password" \
  --value "your_reddit_password"

az keyvault secret set --vault-name "$KEYVAULT_NAME" \
  --name "siphonbot-discord-token" \
  --value "your_discord_token"

az keyvault secret set --vault-name "$KEYVAULT_NAME" \
  --name "siphonbot-discord-webhook" \
  --value "https://discord.com/api/webhooks/..."
```

## How Secrets Flow to Container App

1. **Bicep** stores secrets in Azure Key Vault
2. **CI/CD Workflow** configures Container App to reference Key Vault secrets
3. **Container App** accesses secrets at runtime via UAI (Managed Identity)
4. **Function App** also accesses secrets via UAI for async processing

### Verification

Check that secrets are correctly set in the Container App:

```bash
az containerapp show -g siphon_bot -n siphonbot-app \
  --query "properties.template.containers[].env" -o json
```

Each environment variable using Key Vault should have the format:
```json
{
  "name": "DISCORD_TOKEN",
  "secretRef": "siphonbot-discord-token"
}
```

## Security Best Practices

1. **Never commit secrets to Git** - Always use `.gitignore` to exclude parameter files with real values
2. **Use Key Vault for production** - Store all secrets in Azure Key Vault, never in environment variables
3. **Rotate secrets regularly** - Change passwords, tokens, and credentials periodically
4. **Limit access** - Only grant Key Vault read permissions to necessary identities (Function App, Container App UAI)
5. **Audit access** - Monitor Key Vault access logs for suspicious activity

## Troubleshooting

### "Secret not found" error in Container App

**Cause:** Secret not set in Key Vault, or UAI doesn't have read permissions.

**Fix:**
```bash
# Verify secret exists
az keyvault secret show --vault-name kvsiphonbot --name siphonbot-discord-token

# Verify UAI has permissions
az keyvault show --name kvsiphonbot --query "properties.accessPolicies" -o json
```

### Container App won't start

**Cause:** Missing or invalid secret references.

**Fix:** Check logs:
```bash
az containerapp replica list -n siphonbot-app -g siphon_bot \
  --query "[0]" -o json | grep -i "error\|failed"
```

### Python application says "REDDIT_CLIENT_ID not found"

**Cause:** Environment variables not being read by the container.

**Fix:** Verify secrets are mounted in the container revision:
```bash
az containerapp revision list -n siphonbot-app -g siphon_bot \
  --query "[0].properties.template.containers[].env" -o json
```
