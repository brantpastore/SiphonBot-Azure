Azure Architecture and Tradeoffs for SiphonBot

Overview
- Pattern: Hybrid serverless + container jobs.
  - Frontline: Azure Functions (Consumption or Premium) for webhooks, light API handlers, cron triggers.
  - Workers: Azure Container Apps (jobs) for media downloads, processing, and any tasks requiring custom binaries or longer run-times.
  - Storage: Azure Blob Storage for media and artifacts.
  - Queue/Orchestration: Azure Storage Queue or Service Bus to decouple Functions and Container Apps.
  - Registry: Azure Container Registry (ACR) for container images.
  - Observability: Application Insights + Log Analytics workspace.
  - Secrets: Azure Key Vault + Managed Identity.

Decision factors
- Cost
  - Azure Functions (Consumption): best for spiky, low-volume endpoints — pay-per-execution, zero idle cost.
  - Container Apps jobs: pay for CPU/memory while running; cheaper than App Service for bursty jobs with no always-on needs.
  - App Service (Web App for Containers): higher baseline cost (always-on) — use only if you need persistent background services, sticky sessions, or built-in features.

- Visibility
  - Application Insights integrates across Functions and Container Apps — supports traces, metrics, logs, and alerts.
  - Centralize logs into a Log Analytics workspace for cross-service queries and alerts.

- Usability / Developer Experience
  - Local dev: Functions Core Tools for Functions; Docker for Container Apps.
  - CI/CD: Build container images once and reuse across environments; use GitHub Actions to push to ACR and deploy.
  - Container Apps supports any OS-level dependency in a container (ffmpeg, wget, etc.) making media tasks simple.

Tradeoffs / When to prefer alternatives
- If operations are short (<< 15 minutes) and can run in pure Python with no native binaries, Functions may be used for both handler and worker.
- If tasks require >15 minutes or native tools (ffmpeg, headless browsers), use Container Apps job or App Service on Linux.
- If ultra-low latency and high-throughput HTTP is required, App Service or a dedicated container orchestration with reserved resources may be preferable.

Mapping to this repository
- Candidate Function handlers (lightweight): `python_files/main.py` (entry), `python_files/discord_bot.py` (webhook handling) — can be converted to HTTP-trigger Functions.
- Worker / Downloader: `python_files/apis/media/media_handler.py` and `python_files/media/media_handler.py` (if exists) — run inside a container job with access to required binaries.

Security and Secrets
- Store API keys and tokens in Key Vault and grant access via Managed Identity to Functions and Container Apps.
- Use ACR managed identities or `az acr repository` role assignments to allow pulling images securely.

Observability
- Configure App Insights and instrument Python with `applicationinsights` or `opencensus`/`opentelemetry`.
- Create a few baseline alerts: failed job rate, function error rate, storage egress spikes.

Recommended Next Steps
1. Add Bicep to provision Key Vault, Queue (Storage or Service Bus), and Function App (Consumption or Premium) if desired.
2. Add GitHub Actions to build/push image and deploy both Container Apps and Functions (see `Azurify/github/workflows/ci-cd-full.yml`).
3. Instrument code with App Insights and add sample dashboards.
