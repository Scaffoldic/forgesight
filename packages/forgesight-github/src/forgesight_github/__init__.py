"""ForgeSight GitHub Actions integration — one-line CI bootstrap; run↔commit/PR/job link.

```python
from forgesight_github import bootstrap
bootstrap()   # configure() + attach GITHUB_* metadata + job summary on exit
```
"""

from __future__ import annotations

from .bootstrap import bootstrap, in_github_actions, install, run_exit_hook
from .interceptor import GitHubMetadataInterceptor
from .metadata import GITHUB_ENV_MAP, github_metadata, pr_number
from .oidc import fetch_oidc_token
from .summary import DEFAULT_SUMMARY_METRICS, SummaryCollector, format_summary, write_summary

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_SUMMARY_METRICS",
    "GITHUB_ENV_MAP",
    "GitHubMetadataInterceptor",
    "SummaryCollector",
    "__version__",
    "bootstrap",
    "fetch_oidc_token",
    "format_summary",
    "github_metadata",
    "in_github_actions",
    "install",
    "pr_number",
    "run_exit_hook",
    "write_summary",
]
