"""Foxbridge browser provider plugin.

Mirrors the bundled ``plugins/browser/<vendor>/`` layout: ``provider.py``
holds the provider class; ``__init__.py::register`` instantiates and
registers it with the Hermes plugin context.
"""

from __future__ import annotations

from .provider import FoxbridgeBrowserProvider


def register(ctx) -> None:
    """Register the Foxbridge provider with the plugin context."""
    ctx.register_browser_provider(FoxbridgeBrowserProvider())
