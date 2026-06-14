# forgesight-langfuse

The Langfuse exporter for [ForgeSight](https://github.com/Scaffoldic/forgesight).
Ships ForgeSight records to Langfuse over its OTLP ingest endpoint
(`/api/public/otel`, Basic auth), enriched with the native **`langfuse.*`**
attributes so LLM calls render as **generation** observations, tools as **tool**
observations, and the run's `user`/`session`/`tags` lift to the trace.

```bash
pip install forgesight-langfuse
```

```python
import forgesight
from forgesight_langfuse import LangfuseExporter

forgesight.configure(exporters=[
    LangfuseExporter(public_key="pk-lf-...", secret_key="sk-lf-...",
                     host="https://cloud.langfuse.com"),
])
```

Or by name: `exporters: [{name: langfuse, config: {public_key: …, secret_key: …}}]`.

## Two paths

- **First-party (this package):** native `langfuse.*` observation mapping + the SDK's
  computed cost (`forgesight.usage.cost_usd`) ingested.
- **OTLP-native (no package):** point `forgesight-otel` at
  `https://cloud.langfuse.com/api/public/otel` with `Authorization: Basic base64(pk:sk)`.

Prompt/response content is captured only with `capture_content=True` (off by default).

## License

Apache-2.0
