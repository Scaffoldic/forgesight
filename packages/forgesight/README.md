# forgesight

The batteries-included facade for [ForgeSight](https://github.com/Scaffoldic/forgesight)
— the vendor-neutral, OpenTelemetry-first telemetry SDK for AI agents. This is the
package most users install.

```python
import forgesight

forgesight.configure()
with forgesight.telemetry.agent_run("issue-classifier", version="1.2.0") as run:
    with run.llm_call(provider="anthropic", model="claude-sonnet-4-5") as call:
        ...
        call.record_usage(input=1200, output=350)
```

Re-exports `configure`, `telemetry`, and `instrument` from `forgesight-core`. Add a
backend by installing its package (e.g. `forgesight-otel`) — no code change.

## License

Apache-2.0
