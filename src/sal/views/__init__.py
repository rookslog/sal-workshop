"""View framework and built-in views."""

from sal.views import builtin as _builtin  # noqa: F401
from sal.views.framework import (
    VIEWS,
    PanelResult,
    PanelSpec,
    SignalBundle,
    ViewSpec,
    available_views,
    register_view,
    render_view,
)

__all__ = [
    "PanelResult",
    "PanelSpec",
    "SignalBundle",
    "VIEWS",
    "ViewSpec",
    "available_views",
    "register_view",
    "render_view",
]
