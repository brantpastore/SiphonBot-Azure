"""Minimal Application Insights helper for Python apps.

Usage:
    from Azurify.telemetry.app_insights_snippet import telemetry
    telemetry.track_event('startup')
"""
import os
from applicationinsights import TelemetryClient

_tc = None

def _get_client():
    global _tc
    if _tc is None:
        ikey = os.getenv('APPINSIGHTS_INSTRUMENTATIONKEY') or os.getenv('APPLICATIONINSIGHTS_CONNECTION_STRING')
        if not ikey:
            return None
        _tc = TelemetryClient(ikey)
    return _tc

def track_event(name: str, properties: dict | None = None):
    tc = _get_client()
    if not tc:
        return
    tc.track_event(name, properties)
    tc.flush()

def track_exception(exc: Exception, properties: dict | None = None):
    tc = _get_client()
    if not tc:
        return
    tc.track_exception()
    tc.flush()
