# forgesight-otel

The OpenTelemetry exporter for [ForgeSight](https://github.com/Scaffoldic/forgesight).
Maps ForgeSight `Record`s onto OTLP spans using the OpenTelemetry **GenAI semantic
conventions** — so anything that ingests OTLP (Datadog, Honeycomb, Jaeger, Grafana
Tempo, SigNoz, New Relic, Arize Phoenix) works with no additional package.

```bash
pip install forgesight-otel
```

```python
import forgesight
from forgesight_otel import OTelExporter

forgesight.configure(exporters=[OTelExporter(endpoint="http://otel-collector:4318")])
```

Or enable by name via config (`exporters: [{name: otel, config: {...}}]`) — it
registers under the `forgesight.exporters` entry point.

- Provider discriminator: `gen_ai.provider.name` (legacy `gen_ai.system` opt-in).
- Cost: emitted as the extension attribute `forgesight.usage.cost_usd` (OTel defines
  no cost attribute).
- Prompt/response content is **off by default** (`capture_content=True` to opt in).
- gRPC transport: `pip install forgesight-otel[grpc]` + `protocol="grpc"`.

## License

Apache-2.0
