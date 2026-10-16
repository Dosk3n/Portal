"""Per-call settings for a shared Nexus process; no credentials at import time."""
from dataclasses import dataclass, field
import math

from nexus.config import get_setting


@dataclass(frozen=True)
class PortalSettings:
    base_url: str
    api_key: str = field(repr=False)
    timeout: float = 30.0

    @classmethod
    def load(cls):
        base_url = get_setting("PORTAL_BASE_URL")
        api_key = get_setting("PORTAL_API_KEY")
        if not base_url or not api_key:
            raise ValueError("Configure PORTAL_BASE_URL and PORTAL_API_KEY in Nexus private settings.")
        raw_timeout = get_setting("PORTAL_TIMEOUT_SECONDS") or "30"
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError):
            raise ValueError("PORTAL_TIMEOUT_SECONDS must be a number between 1 and 120.") from None
        if not math.isfinite(timeout) or not 1 <= timeout <= 120:
            raise ValueError("PORTAL_TIMEOUT_SECONDS must be a number between 1 and 120.")
        return cls(base_url, api_key, timeout)
