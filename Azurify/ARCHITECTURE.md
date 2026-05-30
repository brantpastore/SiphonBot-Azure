Azure Architecture and Tradeoffs for SiphonBot

## Overview

**Pattern**: Hybrid serverless + container for decoupled media processing

```
Discord → Container App (bot) → Service Bus Queue → Function App (worker) → Discord Webhook
                ↓ (inline fallback if no queue)
            Process Media Inline
```

- **Frontline**: Azure Container App (always-on) runs the Discord bot, listens for `/scrape` and `/yt` commands
- **Queue**: Azure Service Bus (`siphon-queue`) decouples bot from worker
- **Worker**: Azure Function App (Consumption) processes queued jobs, downloads media, posts results via Discord webhook
- **Registry**: Azure Container Registry (ACR) stores bot container image
- **Observability**: Application Insights + Log Analytics workspace tracks traces, errors, metrics across both services
- **Secrets**: GitHub Actions OIDC federated identity + GitHub secrets for secure deployment
- **Storage**: Ephemeral `/tmp/siphon` volume on Container App for media staging

## Actual Implementation

### Component Breakdown

| Component | Runtime | Purpose | Trigger |
|-----------|---------|---------|---------|
| **Discord Bot** | Container App | Listen for commands; enqueue to Service Bus | Always-on |
| **Media Worker** | Function App (Python 3.11) | Dequeue jobs from Service Bus; download + process media | Service Bus trigger |
| **Service Bus** | Azure Service Bus | Queue/replay job delivery; built-in retry + dead-letter | Both apps |

### Deployment Flow

1. **GitHub Actions** (`ci-cd-full.yml`):
   - Lint/test Python code
   - Build container image, push to ACR
   - Deploy Function App zip package (with bundled dependencies)
   - Deploy Container App (pulls latest image from ACR)

2. **Function App Deployment**:
   - Package Python code + dependencies into zip with `.python_packages/lib/site-packages/` structure
   - Lock build to **Python 3.11** (ensures compiled `.so` files match runtime ABI)
   - Inject Reddit credentials from GitHub secrets as app settings
   - Upload zip via `az functionapp deployment source config-zip`

3. **Container App Deployment**:
   - Pulls fresh image from ACR
   - Mounts secrets as environment variables (via `secretref`)
   - Restarts existing revision or creates new one

### Why Hybrid?

- **Responsiveness**: Discord users get immediate acknowledgment (ephemeral message) while bot enqueues job
- **Reliability**: Failed jobs auto-retry via Service Bus; messages move to dead-letter after max delivery count exceeded
- **Resource Efficiency**: Function App (Consumption plan) scales to zero when idle; only pays for actual execution time
- **Decoupling**: Bot can restart without losing queued jobs; worker can be updated independently

### Decision Factors

**Cost**
- Container App: ~$15–30/month for always-on bot (consumption-based CPU/memory)
- Function App: Pay-per-execution; typically < $5/month for bursty job processing
- Service Bus: ~$11/month for standard queue with 1M operations/month

**Visibility**
- Application Insights: Unified traces, exceptions, metrics from bot + worker
- Log Analytics: Cross-service KQL queries; alert on job failures

**Usability**
- Local dev: `docker compose up` for bot; `func start` for Function App locally
- CI/CD: Single GitHub Actions workflow deploys both; OIDC eliminates manual secret management
- Monitoring: App Insights dashboard tracks queue depth, job duration, error rate

## Mapping to Code

| File(s) | Component | Role |
|---------|-----------|------|
| `python_files/discord_bot.py` | Container App | Command handler; enqueues jobs via `JobQueuePublisher` |
| `python_files/apis/job_queue.py` | Container App | Service Bus client; sends JSON job payloads |
| `azure_functions/process_media/__init__.py` | Function App | Entry point; dequeues and dispatches to `media_processor` |
| `azure_functions/shared/media_processor.py` | Function App | Core logic; downloads media, posts via webhook |
| `.github/workflows/ci-cd-full.yml` | CI/CD | Builds, packages, deploys both services |

## Tradeoffs & Alternatives

### Current Approach (Hybrid + Serverless)
✅ Low cost, auto-scales, decoupled  
❌ Service Bus adds latency (~1–5 seconds per message); webhook requires stable Discord token refresh

### Alternative: Functions Only
✅ Simpler (single runtime)  
❌ Python 3.11 Functions don't support ffmpeg/system binaries; limited to pure Python packages; no persistent background service

### Alternative: App Service (Web App for Containers)
✅ Persistent background processes; built-in load balancing  
❌ Higher baseline cost (~$50–100/month always-on)

### Alternative: AKS
✅ Full orchestration control; can run any workload  
❌ Overkill for bursty media jobs; requires operational overhead

## Security & Observability

### Authentication & Authorization
- GitHub Actions uses **OIDC federated credentials** (no long-lived secrets stored)
- Container App + Function App use **User-Assigned Managed Identity** to pull from ACR and access Key Vault
- Service Bus connection string stored in GitHub secrets (seeded via `Azurify/scripts/bootstrap_github_secrets.sh`)

### Monitoring
- **Application Insights**: Automatically captures Function App traces, exceptions, metrics
- **Alerts**: Dead-letter message count > 0 (indicates job failures)
- **Logs**: Check `azure_functions/process_media/__init__.py` print statements via Azure Portal → Function App → Logs

### Dead-Letter Handling
When a job fails 10 times (Service Bus max delivery count), it moves to the dead-letter queue:
```bash
az servicebus queue show --resource-group siphon_bot --namespace-name siphon-ns \
  --name siphon-queue --query countDetails
```

Inspect dead-lettered messages:
```python
from azure.servicebus import ServiceBusClient

client = ServiceBusClient.from_connection_string(conn_str)
receiver = client.get_queue_receiver(queue_name='siphon-queue', sub_queue='deadletter')
for msg in receiver.peek_messages(max_message_count=10):
    print(msg.body.decode())
```

## Deployment Checklist

1. ✅ Provision Azure resources via `Azurify/infra/main.bicep`
2. ✅ Create GitHub secrets (REDDIT_* , DISCORD_TOKEN, SERVICE_BUS_CONNECTION_STRING)
3. ✅ Configure OIDC federation in Azure (or use `Azurify/scripts/setup_oidc.sh`)
4. ✅ Push to main branch → GitHub Actions runs `ci-cd-full.yml`
5. ✅ Monitor: Check App Insights for traces and errors
6. ✅ Test: Use `/scrape_custom reddit` in Discord

## Future Improvements

- [ ] Add batch job processing (dequeue multiple messages per invocation)
- [ ] Implement job status polling via Discord modal response
- [ ] Add durable orchestration via Durable Functions or Logic Apps
- [ ] Create custom Application Insights dashboard for job metrics
- [ ] Add health check endpoint on Container App for uptime monitoring

