"""``GITHUB_*`` → business-metadata mapping (FR-5), per the OTel VCS / CICD conventions.

Pure: reads the runner environment and parses the PR number from the event payload JSON
(the one field that is not a plain env var). Maps onto the current ``vcs.*`` / ``cicd.*``
semconv, with ``forgesight.github.*`` extensions where no convention exists yet. Absent fields are
omitted, never fabricated.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence

_log = logging.getLogger("forgesight.github")

# GITHUB_* env var → telemetry attribute key (the documented capture set).
GITHUB_ENV_MAP: Mapping[str, str] = {
    "GITHUB_REPOSITORY": "vcs.repository.name",
    "GITHUB_SHA": "vcs.ref.head.revision",
    "GITHUB_REF": "vcs.ref.head.name",
    "GITHUB_RUN_ID": "cicd.pipeline.run.id",
    "GITHUB_RUN_ATTEMPT": "cicd.pipeline.run.attempt",
    "GITHUB_WORKFLOW": "cicd.pipeline.name",
    "GITHUB_JOB": "cicd.pipeline.task.name",
    "GITHUB_ACTOR": "vcs.change.author",  # agentforge extension where no semconv exists
    "GITHUB_EVENT_NAME": "cicd.pipeline.run.trigger",
}
PR_NUMBER_KEY = "vcs.change.id"


def github_metadata(
    *,
    capture_env: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the ``GITHUB_*`` → metadata mapping (repo, sha, ref, run id/attempt, workflow,
    job, actor, event, PR number). ``capture_env`` restricts which ``GITHUB_*`` keys to read.
    """
    source = os.environ if env is None else env
    allowed = set(capture_env) if capture_env is not None else None
    out: dict[str, str] = {}
    for env_key, attr in GITHUB_ENV_MAP.items():
        if allowed is not None and env_key not in allowed:
            continue
        value = source.get(env_key)
        if value:
            out[attr] = value
    pr = pr_number(source)
    if pr is not None:
        out[PR_NUMBER_KEY] = pr
    return out


def pr_number(env: Mapping[str, str]) -> str | None:
    """PR number from the event payload JSON for ``pull_request*`` events, else ``None``."""
    event_name = env.get("GITHUB_EVENT_NAME", "")
    if not event_name.startswith("pull_request"):
        return None
    path = env.get("GITHUB_EVENT_PATH")
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):  # unreadable / malformed payload — don't fabricate
        _log.warning("forgesight-github: could not read event payload at %s", path)
        return None
    candidate = payload.get("pull_request", {}).get("number")
    if candidate is None:
        candidate = payload.get("number")
    return str(candidate) if candidate is not None else None
