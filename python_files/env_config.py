import os
import logging

logger = logging.getLogger(__name__)

# Maps Container App secret names → config variable names.
# These names correspond to what the CI/CD pipeline stores via `az containerapp secret set`.
_SECRET_MAP = {
    "siphonbot-reddit-client-id":       "REDDIT_CLIENT_ID",
    "siphonbot-reddit-client-secret":   "REDDIT_CLIENT_SECRET",
    "siphonbot-reddit-user-agent":      "REDDIT_USER_AGENT",
    "siphonbot-reddit-username":        "REDDIT_USERNAME",
    "siphonbot-reddit-password":        "REDDIT_PASSWORD",
    "siphonbot-discord-token":          "DISCORD_TOKEN",
    "siphonbot-discord-webhook":        "WEBHOOK",
    "siphonbot-service-bus-connection": "SERVICE_BUS_CONNECTION_STRING",
}


def _fetch_from_container_app() -> dict[str, str] | None:
    """
    Fetch secrets from the Container App using the Managed Identity at runtime.

    Requires three env vars (all non-sensitive):
      AZURE_SUBSCRIPTION_ID  — Azure subscription ID
      AZURE_RESOURCE_GROUP   — resource group containing the Container App
      CONTAINER_APP_NAME     — auto-injected by Container Apps at runtime

    Returns a mapping of config variable name → secret value, or None when not
    running in Azure (e.g. local development).
    """
    subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID")
    resource_group = os.environ.get("AZURE_RESOURCE_GROUP")
    container_app_name = os.environ.get("CONTAINER_APP_NAME")  # auto-set by Container Apps

    if not all([subscription_id, resource_group, container_app_name]):
        return None

    try:
        import requests
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        token = credential.get_token("https://management.azure.com/.default").token

        url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.App/containerApps/{container_app_name}"
            f"/listSecrets?api-version=2024-03-01"
        )
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()

        fetched: dict[str, str] = {}
        for secret in resp.json().get("value", []):
            var_name = _SECRET_MAP.get(secret.get("name", ""))
            if var_name:
                fetched[var_name] = secret.get("value", "")

        logger.info("Fetched %d secrets from Container App API.", len(fetched))
        return fetched

    except Exception as exc:
        logger.warning(
            "Could not fetch secrets from Container App API (%s); falling back to env vars.", exc
        )
        return None


def load_env_variables() -> dict[str, str]:
    """
    Load configuration.

    When running on Azure Container Apps, secrets are fetched from the Container
    App secrets store via Managed Identity.  Otherwise (local dev), values are
    read directly from environment variables.
    """
    azure_secrets = _fetch_from_container_app()

    def get(key: str, default: str = "") -> str:
        # Prefer the value from the Container App API; fall back to env var.
        if azure_secrets is not None:
            return azure_secrets.get(key) or os.environ.get(key, default)
        return os.environ.get(key, default)

    return {
        "REDDIT_CLIENT_ID":              get("REDDIT_CLIENT_ID"),
        "REDDIT_CLIENT_SECRET":          get("REDDIT_CLIENT_SECRET"),
        "REDDIT_USER_AGENT":             get("REDDIT_USER_AGENT"),
        "REDDIT_USERNAME":               get("REDDIT_USERNAME"),
        "REDDIT_PASSWORD":               get("REDDIT_PASSWORD"),
        "DISCORD_TOKEN":                 get("DISCORD_TOKEN"),
        "WEBHOOK":                       get("WEBHOOK"),
        "SERVICE_BUS_CONNECTION_STRING": get("SERVICE_BUS_CONNECTION_STRING"),
        "SERVICE_BUS_QUEUE_NAME":        os.environ.get("SERVICE_BUS_QUEUE_NAME", "siphon-queue"),
    }
