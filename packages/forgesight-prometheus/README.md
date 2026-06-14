# forgesight-prometheus

The Prometheus exporter for [ForgeSight](https://github.com/Scaffoldic/forgesight).
Bridges ForgeSight's product metrics + GenAI histograms onto a Prometheus registry
with a pull `/metrics` endpoint (and an optional Pushgateway for short-lived runs).

```bash
pip install forgesight-prometheus
```

```python
import forgesight
from forgesight_prometheus import PrometheusExporter

forgesight.configure(exporters=[PrometheusExporter(port=9464, prefix="agentforge")])
# Prometheus scrapes http://<host>:9464/metrics
```

Or by name via config: `exporters: [{name: prometheus, config: {port: 9464}}]`.

- Labels are cardinality-bounded (agent name / provider / model / status / …);
  `run_id`/`trace_id` are never labels.
- `push_gateway: http://pushgateway:9091` pushes on shutdown for CI / batch runs.

## License

Apache-2.0
