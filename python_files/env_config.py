import os
import logging
import sys

logger = logging.getLogger(__name__)

# Set to True for extra detail in logs (env var LOG_LEVEL=DEBUG also enables this).
_VERBOSE = os.environ.get("LOG_LEVEL", "").upper() == "DEBUG"

# Required keys — startup will fail fast if any of these are missing/empty.
_REQUIRED_KEYS = [
    "DISCORD_TOKEN",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USERNAME",
    "REDDIT_PASSWORD",
]

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

    logger.info(
        "[startup] Azure context: subscription_id=%s resource_group=%s container_app_name=%s",
        subscription_id or "<not set>",
        resource_group or "<not set>",
        container_app_name or "<not set>",
    )

    if not all([subscription_id, resource_group, container_app_name]):
        logger.info(
            "[startup] One or more Azure context vars missing — skipping Container App secret fetch "
            "(running locally or vars not injected). Will fall back to env vars."
        )
        return None

    try:
        import requests
        from azure.identity import DefaultAzureCredential

        logger.info("[startup] Acquiring Managed Identity token for Azure management API...")
        credential = DefaultAzureCredential()
        token = credential.get_token("https://management.azure.com/.default").token
        logger.info("[startup] Token acquired successfully.")

        url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.App/containerApps/{container_app_name}"
            f"/listSecrets?api-version=2024-03-01"
        )
        logger.info("[startup] Calling Container App listSecrets API: %s", url)
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        logger.info("[startup] listSecrets HTTP response: %s", resp.status_code)
        resp.raise_for_status()

        raw_secrets = resp.json().get("value", [])
        logger.info("[startup] API returned %d raw secret entries.", len(raw_secrets))

        fetched: dict[str, str] = {}
        for secret in raw_secrets:
            secret_name = secret.get("name", "")
            var_name = _SECRET_MAP.get(secret_name)
            value = secret.get("value", "")
            if var_name:
                fetched[var_name] = value
                if _VERBOSE:
                    masked = (value[:4] + "****") if value else "<empty>"
                    logger.debug(
                        "[startup] Mapped secret '%s' → %s = %s",
                        secret_name, var_name, masked,
                    )
            else:
                logger.debug("[startup] Unknown secret name '%s' — ignored.", secret_name)

        present = [k for k, v in fetched.items() if v]
        missing = [k for k, v in fetched.items() if not v]
        logger.info(
            "[startup] Fetched %d secrets from Container App API. Present: %s. Empty: %s.",
            len(fetched), present, missing or "none",
        )
        return fetched

    except Exception as exc:
        logger.warning(
            "[startup] Could not fetch secrets from Container App API (%s: %s); "
            "falling back to env vars.",
            type(exc).__name__, exc,
        )
        return None


def _preflight_check(config: dict[str, str]) -> None:
    """
    Validate that all required config keys are present and non-empty.
    Logs a clear error and raises SystemExit so the container fails fast
    with a human-readable message instead of a cryptic downstream TypeError.
    """
    missing = [k for k in _REQUIRED_KEYS if not config.get(k)]
    if missing:
        logger.error(
            "[startup] PREFLIGHT FAILED — the following required config keys are "
            "missing or empty: %s. Check that Container App secrets are set and the "
            "Managed Identity has 'Contributor' role on the Container App resource, "
            "or that the corresponding environment variables are set for local dev.",
            missing,
        )
        sys.exit(1)
    logger.info("[startup] Preflight check passed. All required config keys are present.")


def load_env_variables() -> dict[str, str]:
    """
    Load configuration.

    When running on Azure Container Apps, secrets are fetched from the Container
    App secrets store via Managed Identity.  Otherwise (local dev), values are
    read directly from environment variables.
    """
    logger.info(
        "[startup] load_env_variables called. LOG_LEVEL=%s verbose=%s",
        os.environ.get("LOG_LEVEL", "<not set>"), _VERBOSE,
    )

    azure_secrets = _fetch_from_container_app()

    source = "Container App API" if azure_secrets is not None else "environment variables"
    logger.info("[startup] Secret source: %s", source)

    def get(key: str, default: str = "") -> str:
        # Prefer the value from the Container App API; fall back to env var.
        if azure_secrets is not None:
            val = azure_secrets.get(key) or os.environ.get(key, default)
        else:
            val = os.environ.get(key, default)
        if _VERBOSE:
            present = "present" if val else "MISSING"
            logger.debug("[startup] Config key %s: %s", key, present)
        return val

    config = {
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

    _preflight_check(config)
    return config
