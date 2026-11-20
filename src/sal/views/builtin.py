"""Built-in views registered on package import."""

from __future__ import annotations

from sal.views.audio import loudness_panel, waveform_panel
from sal.views.framework import PanelSpec, ViewSpec, register_view

AUDIO_BASIC_VIEW = register_view(
    ViewSpec(
        name="audio_basic",
        panels=(
            PanelSpec(name="waveform", compute=waveform_panel, height=240),
            PanelSpec(name="loudness", compute=loudness_panel, height=240),
        ),
        requires_audio=True,
    )
)
