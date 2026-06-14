"""Locked error types raised at ``configure()`` when a named integration is unknown.

Fail-fast at bootstrap (architecture §8) rather than a silent mid-run drop. Each names
its entry-point group so the message points at the likely ``pip install``.
"""

from __future__ import annotations


class ExporterNotRegisteredError(LookupError):
    """A named exporter does not resolve in group ``forgesight.exporters``."""


class InterceptorNotRegisteredError(LookupError):
    """A named interceptor does not resolve in group ``forgesight.interceptors``."""


class EventListenerNotRegisteredError(LookupError):
    """A named listener does not resolve in group ``forgesight.listeners``."""


class PricingProviderNotRegisteredError(LookupError):
    """A named pricing provider does not resolve in group ``forgesight.pricing``."""
