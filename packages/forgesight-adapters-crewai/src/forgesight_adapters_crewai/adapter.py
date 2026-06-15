"""``CrewAIAdapter`` — subscribe ForgeSight to the CrewAI event bus.

``instrument()`` registers the translator's handlers on the CrewAI event bus, so an unchanged
crew emits the SDK domain model. The CrewAI SDK is imported lazily (it is the user's own
framework — a heavy tree we don't re-pin), and the bus is injectable so the translation +
lifecycle are tested without it. Idempotent via :class:`~forgesight_core.BaseAdapter`.
"""

from __future__ import annotations

from typing import Any

from forgesight_core import BaseAdapter

from .translator import CrewAIEventTranslator

# CrewAI event-type name → translator handler attribute.
_EVENT_HANDLERS: dict[str, str] = {
    "CrewKickoffStartedEvent": "on_crew_start",
    "CrewKickoffCompletedEvent": "on_crew_end",
    "CrewKickoffFailedEvent": "on_crew_end",
    "AgentExecutionStartedEvent": "on_agent_start",
    "AgentExecutionCompletedEvent": "on_agent_end",
    "AgentExecutionErrorEvent": "on_agent_end",
    "TaskStartedEvent": "on_task_start",
    "TaskCompletedEvent": "on_task_end",
    "TaskFailedEvent": "on_task_end",
    "LLMCallStartedEvent": "on_llm_start",
    "LLMCallCompletedEvent": "on_llm_end",
    "LLMCallFailedEvent": "on_llm_end",
    "ToolUsageStartedEvent": "on_tool_start",
    "ToolUsageFinishedEvent": "on_tool_end",
    "ToolUsageErrorEvent": "on_tool_end",
}


class CrewAIAdapter(BaseAdapter):
    """Auto-instrument CrewAI by subscribing the translator to its event bus."""

    name = "crewai"

    def __init__(self, *, event_bus: Any = None, event_types: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._translator = CrewAIEventTranslator()
        self._bus = event_bus
        self._event_types = event_types
        self._registered: list[tuple[Any, Any]] = []

    @property
    def translator(self) -> CrewAIEventTranslator:
        return self._translator

    def _subscribe(self) -> None:
        bus = self._bus if self._bus is not None else _crewai_bus()
        event_types = self._event_types if self._event_types is not None else _crewai_event_types()
        for type_name, handler_attr in _EVENT_HANDLERS.items():
            event_type = event_types.get(type_name)
            if event_type is None:
                continue
            handler = getattr(self._translator, handler_attr)
            bus.on(event_type)(handler)
            self._registered.append((event_type, handler))

    def _unsubscribe(self) -> None:
        bus = self._bus if self._bus is not None else _crewai_bus()
        off = getattr(bus, "off", None)
        if callable(off):
            for event_type, handler in self._registered:
                off(event_type, handler)
        self._registered.clear()


def _crewai_bus() -> Any:  # pragma: no cover - requires the crewai package
    import importlib

    # dynamic import keeps mypy off the (uninstalled, heavy) crewai package
    return importlib.import_module("crewai.events").crewai_event_bus


def _crewai_event_types() -> dict[str, Any]:  # pragma: no cover - requires the crewai package
    import importlib

    types: dict[str, Any] = {}
    for module in (
        "crewai.events.types.crew_events",
        "crewai.events.types.agent_events",
        "crewai.events.types.task_events",
        "crewai.events.types.llm_events",
        "crewai.events.types.tool_usage_events",
    ):
        mod = importlib.import_module(module)
        for name in _EVENT_HANDLERS:
            if hasattr(mod, name):
                types[name] = getattr(mod, name)
    return types
