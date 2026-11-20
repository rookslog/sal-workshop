"""Built-in views registered on package import."""

from __future__ import annotations

from sal.views.audio import loudness_panel, waveform_panel
from sal.views.framework import PanelSpec, ViewSpec, register_view
from sal.views.video import (
    DEFAULT_TILE_WIDTH,
    FILMSTRIP_HEIGHT,
    black_frozen_panel,
    cuts_panel,
    filmstrip_panel,
)

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

AV_TRANSITION_VIEW = register_view(
    ViewSpec(
        name="av_transition",
        panels=(
            PanelSpec(
                name="filmstrip",
                compute=filmstrip_panel,
                height=FILMSTRIP_HEIGHT,
                default_params={
                    "tile_height": FILMSTRIP_HEIGHT,
                    "tile_width": DEFAULT_TILE_WIDTH,
                },
            ),
            PanelSpec(name="cuts", compute=cuts_panel, height=84),
            PanelSpec(name="black_frozen", compute=black_frozen_panel, height=72),
            PanelSpec(name="waveform", compute=waveform_panel, height=180),
            PanelSpec(name="loudness", compute=loudness_panel, height=200),
        ),
        requires_audio=True,
        requires_video=True,
    )
)
