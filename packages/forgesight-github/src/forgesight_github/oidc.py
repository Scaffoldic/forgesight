"""Best-effort GitHub Actions OIDC id-token fetch (stdlib HTTP, no GitHub SDK).

CI should authenticate to the collector with the runner's short-lived OIDC token rather than
a static secret. The runner exposes a token endpoint via ``ACTIONS_ID_TOKEN_REQUEST_URL`` +
``ACTIONS_ID_TOKEN_REQUEST_TOKEN`` (only when the job has ``id-token: write``). This fetch is
best-effort: any failure returns ``None`` so ``bootstrap`` falls back to configured exporter
auth and never fails the job (P6).
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

_log = logging.getLogger("forgesight.github")

_URL_ENV = "ACTIONS_ID_TOKEN_REQUEST_URL"
_TOKEN_ENV = "ACTIONS_ID_TOKEN_REQUEST_TOKEN"


def fetch_oidc_token(
    *, audience: str | None = None, env: Mapping[str, str] | None = None, timeout: float = 5.0
) -> str | None:
    """Return the runner OIDC id-token, or ``None`` if unavailable / on any error."""
    import os

    source = os.environ if env is None else env
    url = source.get(_URL_ENV)
    bearer = source.get(_TOKEN_ENV)
    if not url or not bearer:
        return None
    if audience:
        url = f"{url}&audience={urllib.parse.quote(audience)}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload: Any = json.loads(response.read().decode("utf-8"))
    except Exception:  # network / parse / auth — best-effort, never raise (P6)
        _log.warning(
            "forgesight-github: OIDC token request failed; falling back to configured auth"
        )
        return None
    value = payload.get("value") if isinstance(payload, dict) else None
    return str(value) if value else None
