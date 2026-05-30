import re
from urllib.parse import urlparse
import ipaddress


def sanitize_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', "_", filename).strip()


def is_safe_url(url):
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname or not parsed.scheme in ("http", "https"):
            return False
        path_lower = parsed.path.lower()
        if path_lower.endswith(
            (".exe", ".bat", ".cmd", ".sh", ".js", ".msi", ".m3u8", ".m3u")
        ):
            return False
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_reserved:
                return False
        except ValueError:
            pass  # hostname is a domain, not an IP — that's fine
        # Block common internal hostnames
        if hostname in ("localhost", "host.docker.internal"):
            return False
        return True
    except Exception:
        return False
