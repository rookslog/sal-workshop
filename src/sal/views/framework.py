"""View and panel framework for rendering deterministic PNG + JSON views."""

from __future__ import annotations

import io
import json
import math
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from sal import __version__
from sal.views.audio import decode_audio_pcm, measure_ebur128
from sal.views.media import CommandRecorder, MediaInfo, probe_media, probe_tool_version
from sal.views.ruler import Ruler, make_ticks
from sal.views.video import VideoEvents, analyze_video_events

if TYPE_CHECKING:
    from matplotlib.axes import Axes

IMAGE_WIDTH = 1600
IMAGE_DPI = 100
PLOT_X0 = 90
PLOT_X1 = 1550
TOP_MARGIN = 48
PANEL_GAP = 54
BOTTOM_MARGIN = 76


def _ensure_mpl_config_dir() -> None:
    """Point MPLCONFIGDIR to a writable cache directory before importing matplotlib."""
    existing = os.environ.get("MPLCONFIGDIR")
    if existing and os.access(existing, os.W_OK):
        return
    uid = getattr(os, "getuid", lambda: 0)()
    cache_dir = Path(tempfile.gettempdir()) / f"sal-mpl-{uid}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(cache_dir)


@dataclass(frozen=True)
class PanelResult:
    """Computed series and drawing callback for one rendered panel."""

    name: str
    params: dict[str, Any]
    data: dict[str, Any]
    draw: Callable[[Axes, Ruler], None]


@dataclass(frozen=True)
class PanelSpec:
    """Definition of a panel type within a view."""

    name: str
    compute: Callable[[SignalBundle, tuple[float, float], Mapping[str, Any]], PanelResult]
    height: int = 240
    default_params: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ViewSpec:
    """Ordered list of panels rendered on a shared time ruler."""

    name: str
    panels: tuple[PanelSpec, ...]
    requires_audio: bool = True
    requires_video: bool = False


class SignalBundle:
    """Lazy cache of decoded signals and recorded external commands for one input."""

    def __init__(self, input_path: Path, media: MediaInfo, recorder: CommandRecorder) -> None:
        self.input_path = input_path
        self.media = media
        self.recorder = recorder
        self._audio_samples: np.ndarray | None = None
        self._ebur128: tuple[list[float], list[float | None], list[float | None]] | None = None
        self._video_events: VideoEvents | None = None

    def audio_samples(self) -> np.ndarray:
        """Return cached decoded PCM float32 audio samples shaped ``(n, channels)``."""
        if self._audio_samples is None:
            self._audio_samples = decode_audio_pcm(self)
        return self._audio_samples

    def ebur128(self) -> tuple[list[float], list[float | None], list[float | None]]:
        """Return cached ``(times, momentary, short_term)`` EBU R 128 measurements."""
        if self._ebur128 is None:
            self._ebur128 = measure_ebur128(self)
        return self._ebur128

    def video_events(self) -> VideoEvents:
        """Return cached frame timestamps, cuts, black, and freeze detections."""
        if self._video_events is None:
            self._video_events = analyze_video_events(self)
        return self._video_events


VIEWS: dict[str, ViewSpec] = {}


def register_view(spec: ViewSpec) -> ViewSpec:
    """Register a view specification by name."""
    VIEWS[spec.name] = spec
    return spec


def available_views() -> tuple[str, ...]:
    """Return sorted registered view names."""
    return tuple(sorted(VIEWS))


def parse_span(span_arg: str | None, duration: float) -> tuple[float, float]:
    """Parse and validate ``--span A:B`` against ``0 <= A < B <= duration``."""
    if span_arg is None:
        return 0.0, duration

    parts = span_arg.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid span {span_arg!r}: expected A:B in seconds")

    try:
        raw_start = float(parts[0])
        raw_end = float(parts[1])
    except ValueError as exc:
        raise ValueError(f"invalid span {span_arg!r}: non-numeric bound") from exc

    if not (math.isfinite(raw_start) and math.isfinite(raw_end)):
        raise ValueError(f"invalid span {span_arg!r}: bounds must be finite")

    if raw_start < 0.0 or raw_end > duration or raw_start >= raw_end:
        raise ValueError(
            f"span {raw_start}:{raw_end} out of range for duration {duration:.3f}s "
            "(require 0 <= A < B <= duration)"
        )

    start = round(raw_start, 3)
    end = round(raw_end, 3)
    if start < 0.0 or end > duration or start >= end:
        raise ValueError(
            f"span {start}:{end} invalid after millisecond rounding for duration {duration:.3f}s"
        )
    return start, end


def _compute_panel_boxes(
    panels: tuple[PanelSpec, ...],
) -> tuple[int, list[tuple[int, int, int, int]]]:
    box_width = PLOT_X1 - PLOT_X0
    boxes: list[tuple[int, int, int, int]] = []
    cursor_y = TOP_MARGIN
    for idx, panel in enumerate(panels):
        if idx > 0:
            cursor_y += PANEL_GAP
        boxes.append((PLOT_X0, cursor_y, box_width, panel.height))
        cursor_y += panel.height
    image_height = cursor_y + BOTTOM_MARGIN
    return image_height, boxes


def _draw_png(
    image_height: int,
    ruler: Ruler,
    ticks: list[dict[str, float | str]],
    panel_results: list[PanelResult],
    boxes: list[tuple[int, int, int, int]],
) -> bytes:
    _ensure_mpl_config_dir()
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    fig = Figure(
        figsize=(IMAGE_WIDTH / IMAGE_DPI, image_height / IMAGE_DPI),
        dpi=IMAGE_DPI,
        facecolor="#ffffff",
    )

    tick_times = [float(tick["t"]) for tick in ticks]
    tick_labels = [str(tick["label"]) for tick in ticks]
    last_index = len(panel_results) - 1

    for idx, (panel, box) in enumerate(zip(panel_results, boxes, strict=True)):
        bx, by, bw, bh = box
        rect = (
            bx / IMAGE_WIDTH,
            (image_height - (by + bh)) / image_height,
            bw / IMAGE_WIDTH,
            bh / image_height,
        )
        ax = fig.add_axes(rect, facecolor="#fafafa")
        ax.margins(x=0.0, y=0.0)
        ax.set_axisbelow(True)
        ax.grid(True, axis="both", color="#e4e7eb", linewidth=0.7, linestyle="-")
        for spine in ax.spines.values():
            spine.set_color("#4a5568")
            spine.set_linewidth(0.8)

        panel.draw(ax, ruler)

        ax.set_xticks(tick_times)
        ax.tick_params(axis="y", direction="out", labelsize=8.5, colors="#222222")
        if idx == last_index:
            ax.set_xticklabels(tick_labels)
            ax.tick_params(axis="x", direction="out", length=5, labelsize=9, colors="#222222")
            ax.set_xlabel("Time", fontsize=9.5, color="#222222", labelpad=8)
        else:
            ax.set_xticklabels([])
            ax.tick_params(axis="x", length=0)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=IMAGE_DPI, metadata={"Software": "sal"})
    return buf.getvalue()


def _write_temp_bytes(directory: Path, prefix: str, data: bytes) -> Path:
    """Write ``data`` to a new temporary file in ``directory``."""
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=prefix)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


def _replace_outputs(out_dir: Path, png_bytes: bytes, json_bytes: bytes) -> tuple[Path, Path]:
    """Write ``view.png`` and ``view.json`` via temp files without following symlinks."""
    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / "view.png"
    json_path = out_dir / "view.json"
    tmp_png = _write_temp_bytes(out_dir, ".view.png.", png_bytes)
    tmp_json: Path | None = None
    backup_png: Path | None = None
    png_replaced = False
    try:
        tmp_json = _write_temp_bytes(out_dir, ".view.json.", json_bytes)
        if os.path.lexists(png_path):
            fd, backup_name = tempfile.mkstemp(dir=out_dir, prefix=".view.png.bak.")
            os.close(fd)
            backup_png = Path(backup_name)
            os.replace(png_path, backup_png)
        os.replace(tmp_png, png_path)
        png_replaced = True
        os.replace(tmp_json, json_path)
    except BaseException:
        if png_replaced or (backup_png is not None and not os.path.lexists(png_path)):
            if backup_png is not None and os.path.lexists(backup_png):
                try:
                    os.replace(backup_png, png_path)
                except OSError:
                    pass
            elif png_replaced:
                png_path.unlink(missing_ok=True)
        elif backup_png is not None:
            backup_png.unlink(missing_ok=True)
        tmp_png.unlink(missing_ok=True)
        if tmp_json is not None:
            tmp_json.unlink(missing_ok=True)
        raise
    if backup_png is not None:
        backup_png.unlink(missing_ok=True)
    return png_path, json_path


def render_view(
    view_name: str,
    input_path: Path,
    out_dir: Path,
    span_arg: str | None = None,
) -> tuple[Path, Path]:
    """Validate inputs, compute panel data, render PNG/JSON in memory, then write ``out_dir``."""
    if view_name not in VIEWS:
        names = ", ".join(available_views()) or "(none)"
        raise ValueError(f"unknown view {view_name!r}; available views: {names}")

    if out_dir.exists() and not out_dir.is_dir():
        raise ValueError(f"output path exists and is not a directory: {out_dir}")

    spec = VIEWS[view_name]
    ffmpeg_version = probe_tool_version("ffmpeg")
    recorder = CommandRecorder(input_path)
    media = probe_media(input_path, recorder)

    if spec.requires_audio and not media.has_audio:
        raise ValueError("input file has no audio stream")
    if spec.requires_video and not media.has_video:
        raise ValueError("input file has no video stream")

    t0, t1 = parse_span(span_arg, media.duration)
    image_height, boxes = _compute_panel_boxes(spec.panels)
    ruler = Ruler(x0=PLOT_X0, x1=PLOT_X1, t0=t0, t1=t1)
    ticks = make_ticks(ruler)

    signals = SignalBundle(input_path=input_path, media=media, recorder=recorder)
    panel_results = [
        panel_spec.compute(signals, (t0, t1), panel_spec.default_params or {})
        for panel_spec in spec.panels
    ]

    png_bytes = _draw_png(image_height, ruler, ticks, panel_results, boxes)

    payload: dict[str, Any] = {
        "schema": "sal.view/1",
        "view": spec.name,
        "input": {
            "sha256": media.sha256,
            "duration": media.duration,
        },
        "span": {
            "start": t0,
            "end": t1,
        },
        "image": {
            "file": "view.png",
            "width": IMAGE_WIDTH,
            "height": image_height,
        },
        "ruler": {
            "x0": ruler.x0,
            "x1": ruler.x1,
            "t0": ruler.t0,
            "t1": ruler.t1,
            "ticks": ticks,
        },
        "panels": [
            {
                "name": result.name,
                "box": list(box),
                "params": result.params,
                "data": result.data,
            }
            for result, box in zip(panel_results, boxes, strict=True)
        ],
        "commands": recorder.commands,
        "tool": {
            "sal": __version__,
            "ffmpeg": ffmpeg_version,
        },
    }

    json_text = (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    )
    json_bytes = json_text.encode("utf-8")

    # Write only after all validation, subprocess calls, and rendering succeed.
    return _replace_outputs(out_dir, png_bytes, json_bytes)
