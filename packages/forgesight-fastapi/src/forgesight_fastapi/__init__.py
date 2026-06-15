"""ForgeSight FastAPI integration — request↔run correlation + flush-on-shutdown.

```python
from fastapi import FastAPI
from forgesight_fastapi import AgentForgeMiddleware, sdk_lifespan

app = FastAPI(lifespan=sdk_lifespan)
app.add_middleware(AgentForgeMiddleware)
```
"""

from __future__ import annotations

from ._config import DEFAULT_EXCLUDE_PATHS, SPAN_KINDS, install
from .lifespan import sdk_lifespan
from .middleware import AgentForgeMiddleware, HTTPServerError

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_EXCLUDE_PATHS",
    "SPAN_KINDS",
    "AgentForgeMiddleware",
    "HTTPServerError",
    "__version__",
    "install",
    "sdk_lifespan",
]
