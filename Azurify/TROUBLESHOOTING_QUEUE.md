# SiphonBot Queue Processing Troubleshooting Guide

This guide covers diagnosing and fixing issues with the Service Bus queue processing pipeline (Discord bot → Service Bus → Function App worker → Discord webhook).

---

## Quick Diagnostics

### Check Queue Health

```bash
RG=siphon_bot
NS=$(az servicebus namespace list -g "$RG" --query "[0].name" -o tsv)
az servicebus queue show -g "$RG" --namespace-name "$NS" --name siphon-queue \
  --query "{active:countDetails.activeMessageCount,dead:countDetails.deadLetterMessageCount}" -o json
```

**Healthy state**:
- `active`: 0 (messages processed immediately)
- `dead`: Stable or only increasing when jobs genuinely fail (not credentials issue)

**Unhealthy state**:
- `active` > 10: Messages queuing up, likely worker crashed
- `dead` growing: Worker crashed or failing to post results

---

## Issue: Jobs Dead-Lettering Immediately

### Symptoms
- Queue shows messages moving to dead-letter after 10 attempts
- Application Insights shows repeated Service Bus trigger failures
- Discord bot appears to enqueue successfully

### Root Causes & Fixes

#### 1. Missing or Invalid Reddit Credentials

**Check**: Function App has Reddit credentials

```bash
az functionapp config appsettings list -g siphon_bot -n siphonbot-func \
  --query "[?name=='REDDIT_CLIENT_ID' || name=='REDDIT_USERNAME'].{name:name, value:value}" -o table
```

**Expected**: All 5 Reddit variables present (ID, SECRET, USERNAME, PASSWORD, USER_AGENT)

**If missing**:
```bash
# Re-deploy to inject secrets:
cd Azurify && bash deploy/azure-deploy.sh
# OR manually:
az functionapp config appsettings set -g siphon_bot -n siphonbot-func --settings \
  REDDIT_CLIENT_ID="<value>" \
  REDDIT_CLIENT_SECRET="<value>" \
  REDDIT_USERNAME="<value>" \
  REDDIT_PASSWORD="<value>" \
  REDDIT_USER_AGENT="<value>"
```

#### 2. HTTP 404 Posting to Stale Webhook URL

**Check**: Function App logs for webhook errors

```bash
az monitor app-insights query --app siphonbot-insights -g siphon_bot \
  --analytics-query "traces | where message has '404' or message has 'webhook' | project timestamp, message | order by timestamp desc | take 20" -o table
```

**If you see 404 errors**:
- The interaction webhook URL became invalid (Discord invalidates old URLs)
- **Fix**: Ensure `discord_bot.py` uses `_resolve_job_webhook_url()` to extract per-interaction webhook
- Or manually clear dead-letter and let bot re-enqueue with fresh webhook URL

#### 3. Missing Python Dependencies (aiohttp, praw, etc.)

**Check**: Function App package contents

```bash
# Get WEBSITE_RUN_FROM_PACKAGE URL
FUNC_URL=$(az functionapp config appsettings list -g siphon_bot -n siphonbot-func \
  --query "[?name=='WEBSITE_RUN_FROM_PACKAGE'].value | [0]" -o tsv)

# List what's in the deployed package
# (requires downloading and unzipping; often easier to check logs)

# Or check logs directly:
az monitor app-insights query --app siphonbot-insights -g siphon_bot \
  --analytics-query "traces | where message has 'ModuleNotFoundError' or message has 'aiohttp' | project timestamp, message" -o table
```

**If you see `ModuleNotFoundError`**:
- Deployed zip doesn't include `.python_packages` directory
- **Fix**: Ensure CI/CD runs:
  ```bash
  python -m pip install -r azure_functions/requirements.txt \
    -t function_package/.python_packages/lib/site-packages
  ```

#### 4. Python ABI Mismatch (cpython-311 vs cpython-312)

**Symptom**: Function crashes immediately with `.so` import error (e.g., aiohttp, ujson)

**Check**: Function App Python version vs build Python version

```bash
# Check runtime Python version
az functionapp config appsettings list -g siphon_bot -n siphonbot-func \
  --query "[?name=='FUNCTIONS_WORKER_RUNTIME'].value" -o tsv
# Should show: python

# Check logs for ABI mismatch
az monitor app-insights query --app siphonbot-insights -g siphon_bot \
  --analytics-query "traces | where message has 'cpython' or message has '_http_parser' or message has '.so' | project timestamp, message" -o table
```

**If you see cpython-312 errors**:
- CI/CD used Python 3.12 to build, but runtime is Python 3.11
- **Fix**: Ensure `.github/workflows/ci-cd-full.yml` has:
  ```yaml
  - name: Set up Python for Function packaging
    uses: actions/setup-python@v4
    with:
      python-version: '3.11'
  ```

#### 5. Type Annotation Error (ServiceBusQueueMessage vs ServiceBusMessage)

**Symptom**: 2ms crash loop; Function App never initializes

**Check**: Exact exception type

```bash
az monitor app-insights query --app siphonbot-insights -g siphon_bot \
  --analytics-query "exceptions | where outerMessage has 'ServiceBusQueueMessage' or outerMessage has 'AttributeError' | project timestamp, outerMessage, innerMostMessage" -o table
```

**If you see `AttributeError: module 'azure.functions' has no attribute 'ServiceBusQueueMessage'`**:
- `azure_functions/process_media/__init__.py` uses wrong type annotation
- **Fix**: Change to `func.ServiceBusMessage`:
  ```python
  def main(msg: func.ServiceBusMessage) -> None:  # NOT ServiceBusQueueMessage
  ```

---

## Issue: Function App Won't Start

### Symptoms
- Azure Portal shows deployment status "Failed"
- Application Insights has no traces (never initialized)

### Diagnosis

```bash
# Check deployment status
az functionapp deployment show -g siphon_bot -n siphonbot-func --slot production

# Check if any errors during sync_triggers
az functionapp sync-triggers -g siphon_bot -n siphonbot-func
```

### Fixes

1. **Verify `.python_packages` directory exists in zip**:
   ```bash
   # Manually check zip structure (if you have local copy):
   unzip -l siphonbot-func.zip | grep ".python_packages"
   ```

2. **Re-deploy fresh**:
   ```bash
   cd Azurify && bash deploy/azure-deploy.sh
   ```

3. **Check app service logs**:
   ```bash
   az webapp log config -n siphonbot-func -g siphon_bot --application-logging true
   sleep 5
   timeout 30 az webapp log tail -g siphon_bot -n siphonbot-func 2>&1 | head -100
   ```

---

## Issue: Jobs Process but Results Don't Post

### Symptoms
- Application Insights shows "Job completed" trace
- No results appear in Discord channel
- No webhook errors in logs

### Diagnosis

```bash
# Check for webhook failures
az monitor app-insights query --app siphonbot-insights -g siphon_bot \
  --analytics-query "traces | where message has 'webhook' or message has 'Failed to followup' | project timestamp, message | order by timestamp desc | take 50" -o table
```

### Fixes

1. **Webhook URL expired**:
   - Rerun `/scrape_custom` command to generate fresh webhook URL
   - Or manually clear dead-letter and re-enqueue

2. **Webhook authentication failed**:
   - Verify `DISCORD_TOKEN` and `WEBHOOK` are correct in Container App
   - Test manually: `curl -X POST <webhook_url> -H "Content-Type: application/json" -d '{"content":"test"}'`

3. **Function never reaches `safe_followup()` call**:
   - Check if Reddit API call failed (check reddit_handler logs)
   - Verify Reddit credentials have permission to access subreddit

---

## Clearing the Dead-Letter Queue (when needed)

**WARNING**: This deletes failed messages. Use only after fixing the root cause.

```bash
# Peek at dead-letter messages first
RG=siphon_bot
NS=$(az servicebus namespace list -g "$RG" --query "[0].name" -o tsv)
az servicebus queue show -g "$RG" --namespace-name "$NS" --name siphon-queue \
  --query "countDetails.deadLetterMessageCount"

# Clear dead-letter queue (via Azure CLI or Python SDK)
# Python approach:
python - <<'PY'
from azure.servicebus import ServiceBusClient
import subprocess

conn = subprocess.check_output([
  'az','functionapp','config','appsettings','list','-g','siphon_bot','-n','siphonbot-func',
  '--query',"[?name=='SERVICE_BUS_CONNECTION_STRING'].value | [0]",'-o','tsv'
], text=True).strip()

with ServiceBusClient.from_connection_string(conn) as client:
    receiver = client.get_queue_receiver(queue_name='siphon-queue', sub_queue='deadletter')
    with receiver:
        for msg in receiver.receive_messages(max_message_count=100):
            receiver.complete_message(msg)
            print(f"Deleted: {msg.message_id}")
PY
```

---

## Monitoring & Alerting

### Create an Alert for Dead-Letter Queue Growth

```bash
# Set up alert rule for DLQ > 0
az monitor metrics alert create \
  --name "SiphonBot DLQ Growing" \
  --resource-group siphon_bot \
  --resource /subscriptions/<sub>/resourcegroups/siphon_bot/providers/microsoft.servicebus/namespaces/siphon-ns \
  --scopes /subscriptions/<sub>/resourcegroups/siphon_bot/providers/microsoft.servicebus/namespaces/siphon-ns \
  --condition "avg DeadLetterMessage > 5" \
  --window-size 5m \
  --evaluation-frequency 1m \
  --action email --email-receiver owner@example.com
```

### Dashboard Query (Application Insights KQL)

```kusto
traces
| where cloud_RoleName == "siphonbot-func"
| where message has "Processing job" or message has "Job completed" or message has "ERROR"
| summarize 
    SuccessCount = countif(message has "Job completed"),
    ErrorCount = countif(message has "ERROR"),
    JobsProcessed = count()
    by bin(timestamp, 5m)
| order by timestamp desc
| take 20
```

---

## Recovery Playbook

If the queue is backing up:

1. **Diagnose** (see "Issue" sections above)
2. **Fix** (apply corresponding fix)
3. **Verify** Function App is healthy:
   ```bash
   az functionapp show -g siphon_bot -n siphonbot-func --query state
   # Should be: "Running"
   ```
4. **Restart** Function App if needed:
   ```bash
   az functionapp restart -g siphon_bot -n siphonbot-func
   ```
5. **Clear** dead-letter (only after confirming fix works):
   ```bash
   # Use Python script above
   ```
6. **Monitor** next 10 minutes:
   ```bash
   watch -n 5 'az servicebus queue show -g siphon_bot --namespace-name siphon-ns --name siphon-queue --query countDetails'
   ```

---

## Contact & Debug Mode

If stuck, enable verbose logging:

```bash
# On Container App (bot)
az containerapp update -g siphon_bot -n siphonbot-app \
  --set-env-vars LOG_LEVEL=DEBUG

# Check logs
az containerapp logs show -n siphonbot-app -g siphon_bot --follow=false --tail 200
```

Then run `/scrape_custom <subreddit>` and collect logs for analysis.
