App Insights instrumentation for SiphonBot

This folder contains a minimal Python snippet and instructions to instrument the bot with Application Insights.

Quick steps
1. Add the App Insights connection string or instrumentation key to your environment (in Azure this is provided by the resource and will be injected by the platform):
   - `APPINSIGHTS_INSTRUMENTATIONKEY` or `APPLICATIONINSIGHTS_CONNECTION_STRING`
2. Install the simple dependency locally (for the snippet below):
```bash
pip install applicationinsights
```
3. Import and initialize the snippet in your app startup (e.g., in `python_files/utils.py` or `main.py`).

Notes
- For richer telemetry, consider using OpenTelemetry with the Azure Monitor exporter.
- For Functions, the Functions host can automatically connect to Application Insights when the connection string is provided.
