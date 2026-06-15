"""ForgeSight CrewAI adapter — auto-instrument CrewAI crews via the event bus."""

from __future__ import annotations

from .adapter import CrewAIAdapter
from .translator import CrewAIEventTranslator

__version__ = "0.1.0"

__all__ = ["CrewAIAdapter", "CrewAIEventTranslator", "__version__"]
