"""Video analysis, interval operations, filmstrip extraction, and video panels."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from sal.views.media import INPUT_PLACEHOLDER
from sal.views.ruler import Ruler, x_to_time

if TYPE_CHECKING:
    from matplotlib.axes import Axes

    from sal.views.framework import PanelResult, SignalBundle

DEFAULT_TILE_WIDTH = 92
FILMSTRIP_HEIGHT = 72
CUT_THRESHOLD = 10.0
CUTS_Y_MAX = 100.0
BLACK_MIN_DURATION = 0.2
BLACK_PIX_TH = 0.1
FREEZE_MIN_DURATION = 0.2
FREEZE_NOISE = 0.003

CUT_COLOR = "#dc2626"
THRESHOLD_COLOR = "#64748b"
BLACK_COLOR = "#111827"
FROZEN_COLOR = "#0284c7"
TILE_DIVIDER_VALUE = 32


@dataclass(frozen=True)
class VideoEvents:
    """Decoded frame timestamps and raw scene-change, black, and freeze detections."""

    frame_times: list[tuple[int, float]]
    cuts: list[tuple[float, float]]
    black_raw: list[tuple[float, float]]
    frozen_raw: list[tuple[float, float]]


def tile_layout(box_x: int, box_width: int, tile_width: int) -> list[tuple[int, int]]:
    """Return ``(x, width)`` pairs tiling ``[box_x, box_x + box_width)`` left to right."""
    if (
        isinstance(box_x, bool)
        or isinstance(box_width, bool)
        or isinstance(tile_width, bool)
        or not isinstance(box_x, int)
        or not isinstance(box_width, int)
        or not isinstance(tile_width, int)
    ):
        raise ValueError("box_x, box_width, and tile_width must be integers")
    if box_width < 1:
        raise ValueError(f"box_width ({box_width}) must be at least 1")
    if tile_width < 1:
        raise ValueError(f"tile_width ({tile_width}) must be at least 1")

    tiles: list[tuple[int, int]] = []
    offset = 0
    while offset < box_width:
        width = min(tile_width, box_width - offset)
        tiles.append((box_x + offset, width))
        offset += width
    return tiles


def merge_intervals(a: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sort intervals, drop non-positive ones, and merge overlapping or touching intervals."""
    valid = sorted((float(start), float(end)) for start, end in a if start < end)
    if not valid:
        return []

    merged: list[tuple[float, float]] = [valid[0]]
    for start, end in valid[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def subtract_intervals(
    a: Sequence[tuple[float, float]],
    b: Sequence[tuple[float, float]],
    min_length: float = 0.0,
) -> list[tuple[float, float]]:
    """Return sorted parts of ``merge_intervals(a)`` not covered by ``b``."""
    merged_a = merge_intervals(a)
    if not merged_a:
        return []
    merged_b = merge_intervals(b)

    result: list[tuple[float, float]] = []
    b_idx = 0
    b_len = len(merged_b)

    for a_start, a_end in merged_a:
        while b_idx < b_len and merged_b[b_idx][1] <= a_start:
            b_idx += 1

        cursor = a_start
        scan = b_idx
        while scan < b_len and merged_b[scan][0] < a_end:
            b_start, b_end = merged_b[scan]
            if b_start > cursor:
                _append_if_long_enough(result, cursor, b_start, min_length)
            cursor = max(cursor, b_end)
            if cursor >= a_end:
                break
            scan += 1

        if cursor < a_end:
            _append_if_long_enough(result, cursor, a_end, min_length)

    return result


def _append_if_long_enough(
    target: list[tuple[float, float]],
    start: float,
    end: float,
    min_length: float,
) -> None:
    if end <= start:
        return
    if min_length > 0.0 and (end - start) < min_length:
        return
    target.append((start, end))


def clip_intervals(
    a: Sequence[tuple[float, float]],
    start: float,
    end: float,
) -> list[tuple[float, float]]:
    """Return ``merge_intervals(a)`` clipped to ``[start, end]``, dropping empty parts."""
    if start >= end:
        return []
    clipped: list[tuple[float, float]] = []
    for iv_start, iv_end in merge_intervals(a):
        c_start = max(iv_start, float(start))
        c_end = min(iv_end, float(end))
        if c_start < c_end:
            clipped.append((c_start, c_end))
    return clipped


def _parse_float(raw: str) -> float | None:
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value


def analyze_video_events(signals: SignalBundle) -> VideoEvents:
    """Run one ffmpeg pass for frame timestamps, cuts, blackdetect, and freezedetect."""
    if not signals.media.has_video:
        raise ValueError("input file has no video stream")

    vf = (
        f"blackdetect=d={BLACK_MIN_DURATION}:pix_th={BLACK_PIX_TH},"
        f"freezedetect=n={FREEZE_NOISE}:d={FREEZE_MIN_DURATION},"
        f"scdet=threshold={CUT_THRESHOLD},"
        "metadata=mode=print:file=-"
    )
    template = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        INPUT_PLACEHOLDER,
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        vf,
        "-f",
        "null",
        "-",
    ]
    result = signals.recorder.run(template, text=True)
    stdout = result.stdout if isinstance(result.stdout, str) else result.stdout.decode("utf-8")

    frame_times: list[tuple[int, float]] = []
    cuts: list[tuple[float, float]] = []
    black_raw: list[tuple[float, float]] = []
    frozen_raw: list[tuple[float, float]] = []

    current_frame: int | None = None
    current_pts: float | None = None
    current_scd_score: float | None = None
    current_scd_time: float | None = None
    open_black_start: float | None = None
    open_freeze_start: float | None = None

    def flush_frame() -> None:
        if current_frame is not None and current_pts is not None:
            frame_times.append((current_frame, current_pts))
        if (
            current_scd_time is not None
            and current_scd_score is not None
            and current_scd_score >= CUT_THRESHOLD
        ):
            cuts.append((round(current_scd_time, 3), round(current_scd_score, 3)))

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("frame:"):
            flush_frame()
            current_frame = None
            current_pts = None
            current_scd_score = None
            current_scd_time = None
            for token in line.split():
                if token.startswith("frame:"):
                    try:
                        current_frame = int(token.split(":", 1)[1])
                    except ValueError:
                        current_frame = None
                elif token.startswith("pts_time:"):
                    parsed = _parse_float(token.split(":", 1)[1])
                    if parsed is not None and parsed >= 0.0:
                        current_pts = parsed
        elif line.startswith("lavfi.black_start="):
            parsed = _parse_float(line.split("=", 1)[1])
            if parsed is not None and open_black_start is None:
                open_black_start = max(0.0, parsed)
        elif line.startswith("lavfi.black_end="):
            parsed = _parse_float(line.split("=", 1)[1])
            if parsed is not None and open_black_start is not None:
                start_t = round(open_black_start, 3)
                end_t = round(parsed, 3)
                if start_t < end_t:
                    black_raw.append((start_t, end_t))
                open_black_start = None
        elif line.startswith("lavfi.freezedetect.freeze_start="):
            parsed = _parse_float(line.split("=", 1)[1])
            if parsed is not None and open_freeze_start is None:
                open_freeze_start = max(0.0, parsed)
        elif line.startswith("lavfi.freezedetect.freeze_end="):
            parsed = _parse_float(line.split("=", 1)[1])
            if parsed is not None and open_freeze_start is not None:
                start_t = round(open_freeze_start, 3)
                end_t = round(parsed, 3)
                if start_t < end_t:
                    frozen_raw.append((start_t, end_t))
                open_freeze_start = None
        elif line.startswith("lavfi.scd.score="):
            current_scd_score = _parse_float(line.split("=", 1)[1])
        elif line.startswith("lavfi.scd.time="):
            parsed = _parse_float(line.split("=", 1)[1])
            if parsed is not None and parsed >= 0.0:
                current_scd_time = parsed

    flush_frame()

    duration = round(signals.media.duration, 3)
    if open_black_start is not None:
        start_t = round(open_black_start, 3)
        if duration - start_t >= BLACK_MIN_DURATION - 1e-6:
            black_raw.append((start_t, duration))
    if open_freeze_start is not None:
        start_t = round(open_freeze_start, 3)
        if duration - start_t >= FREEZE_MIN_DURATION - 1e-6:
            frozen_raw.append((start_t, duration))

    if not frame_times:
        raise ValueError("ffmpeg produced no video frames")

    cuts.sort(key=lambda item: item[0])
    return VideoEvents(
        frame_times=frame_times,
        cuts=cuts,
        black_raw=merge_intervals(black_raw),
        frozen_raw=merge_intervals(frozen_raw),
    )


def _select_tile_frames(
    in_span: list[tuple[int, float]],
    targets: list[float],
) -> list[tuple[int, float]]:
    """Choose one frame per tile, strictly increasing in index when enough frames exist."""
    count = len(targets)
    if len(in_span) >= count:
        chosen: list[tuple[int, float]] = []
        prev_k = -1
        for i, target in enumerate(targets):
            min_k = prev_k + 1
            max_k = len(in_span) - (count - i)
            best_k = min(
                range(min_k, max_k + 1),
                key=lambda k: (abs(in_span[k][1] - target), k),
            )
            chosen.append(in_span[best_k])
            prev_k = best_k
        return chosen

    return [min(in_span, key=lambda item: abs(item[1] - target)) for target in targets]


def _extract_frames_rgb(
    signals: SignalBundle,
    frame_indices: Sequence[int],
    tile_width: int,
    tile_height: int,
) -> dict[int, np.ndarray]:
    """Decode and scale-crop selected video frames to ``(tile_height, tile_width, 3)``."""
    unique_indices = sorted(set(frame_indices))
    select_expr = "+".join(f"eq(n\\,{idx})" for idx in unique_indices)
    vf = (
        f"select={select_expr},"
        f"scale={tile_width}:{tile_height}:force_original_aspect_ratio=increase,"
        f"crop={tile_width}:{tile_height}"
    )
    template = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        INPUT_PLACEHOLDER,
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        vf,
        "-fps_mode",
        "passthrough",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    result = signals.recorder.run(template, text=False)
    raw = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode("latin-1")
    frame_bytes = tile_height * tile_width * 3
    expected_bytes = len(unique_indices) * frame_bytes
    if len(raw) < expected_bytes:
        raise RuntimeError(
            f"ffmpeg frame extraction returned {len(raw)} bytes, expected {expected_bytes}"
        )
    decoded = np.frombuffer(raw[:expected_bytes], dtype=np.uint8).reshape(
        len(unique_indices), tile_height, tile_width, 3
    )
    return {idx: decoded[pos] for pos, idx in enumerate(unique_indices)}


def _annotate_title(ax: Axes, title: str) -> None:
    ax.annotate(
        title,
        xy=(0.0, 1.0),
        xycoords="axes fraction",
        xytext=(0.0, 6.0),
        textcoords="offset points",
        ha="left",
        va="bottom",
        fontsize=10,
        fontweight="bold",
        color="#111111",
        annotation_clip=False,
    )


def filmstrip_panel(
    signals: SignalBundle,
    span: tuple[float, float],
    params: Mapping[str, Any],
) -> PanelResult:
    """Extract and tile video frames across ``span`` aligned to the shared ruler."""
    from sal.views.framework import PLOT_X0, PLOT_X1, PanelResult

    t0, t1 = span
    box_x = int(params.get("box_x", PLOT_X0))
    box_width = int(params.get("box_width", PLOT_X1 - PLOT_X0))
    tile_height = int(params.get("tile_height", FILMSTRIP_HEIGHT))
    requested_tile_width = int(params.get("tile_width", DEFAULT_TILE_WIDTH))

    events = signals.video_events()
    in_span = [(idx, pt) for idx, pt in events.frame_times if t0 - 1e-6 <= pt <= t1 + 1e-6]
    if not in_span:
        raise ValueError("selected span contains no video frames")

    tile_width = requested_tile_width
    tiles = tile_layout(box_x, box_width, tile_width)
    if len(in_span) < len(tiles) and len(in_span) >= 4:
        tile_width = max(1, math.ceil(box_width / len(in_span)))
        tiles = tile_layout(box_x, box_width, tile_width)

    ruler = Ruler(x0=box_x, x1=box_x + box_width, t0=t0, t1=t1)
    targets = [x_to_time(tx + tw / 2.0, ruler) for tx, tw in tiles]
    chosen = _select_tile_frames(in_span, targets)

    frame_map = _extract_frames_rgb(
        signals,
        [frame_idx for frame_idx, _ in chosen],
        tile_width=tile_width,
        tile_height=tile_height,
    )

    strip = np.empty((tile_height, box_width, 3), dtype=np.uint8)
    frames_data: list[dict[str, int | float]] = []
    for i, ((tx, tw), (frame_idx, pts_time)) in enumerate(zip(tiles, chosen, strict=True)):
        rel_x = tx - box_x
        img = frame_map[frame_idx]
        if tw == tile_width:
            strip[:, rel_x : rel_x + tw, :] = img
        else:
            crop_x = (tile_width - tw) // 2
            strip[:, rel_x : rel_x + tw, :] = img[:, crop_x : crop_x + tw, :]
        if i > 0 and tw >= 4:
            strip[:, rel_x, :] = TILE_DIVIDER_VALUE

        clamped_t = min(max(round(pts_time, 3), t0), t1)
        frames_data.append({"t": clamped_t, "x": tx, "width": tw})

    resolved_params: dict[str, Any] = {
        "tile_height": tile_height,
        "tile_width": tile_width,
    }
    data: dict[str, Any] = {"frames": frames_data}

    def draw(ax: Axes, panel_ruler: Ruler) -> None:
        ax.grid(False)
        ax.set_xlim(panel_ruler.t0, panel_ruler.t1)
        ax.set_ylim(0.0, 1.0)
        ax.set_yticks([])
        ax.set_ylabel("Frames", fontsize=9, color="#222222")
        ax.imshow(
            strip,
            extent=(panel_ruler.t0, panel_ruler.t1, 0.0, 1.0),
            aspect="auto",
            interpolation="nearest",
            origin="upper",
            zorder=2,
        )
        _annotate_title(ax, "filmstrip (sampled frames)")

    return PanelResult(name="filmstrip", params=resolved_params, data=data, draw=draw)


def cuts_panel(
    signals: SignalBundle,
    span: tuple[float, float],
    params: Mapping[str, Any],
) -> PanelResult:
    """Compute and draw hard picture cuts over ``span``."""
    from sal.views.framework import PanelResult

    t0, t1 = span
    threshold = float(params.get("threshold", CUT_THRESHOLD))
    y_max = float(params.get("y_max", CUTS_Y_MAX))

    events = signals.video_events()
    panel_events = [
        {"score": score, "t": t} for t, score in events.cuts if t0 <= t <= t1 and score >= threshold
    ]

    resolved_params: dict[str, Any] = {
        "detector": "scdet",
        "threshold": threshold,
        "y_max": y_max,
        "y_min": 0.0,
    }
    data: dict[str, Any] = {"events": panel_events}

    ts = [float(ev["t"]) for ev in panel_events]
    scores = [min(float(ev["score"]), y_max) for ev in panel_events]

    def draw(ax: Axes, ruler: Ruler) -> None:
        ax.set_xlim(ruler.t0, ruler.t1)
        ax.set_ylim(0.0, y_max)
        ax.set_yticks([0.0, 50.0, 100.0])
        ax.set_ylabel("Score", fontsize=9, color="#222222")
        ax.axhline(threshold, color=THRESHOLD_COLOR, linewidth=0.9, linestyle="--", zorder=2)
        if ts:
            ax.vlines(ts, 0.0, scores, color=CUT_COLOR, linewidth=2.0, zorder=3)
            ax.scatter(ts, scores, color=CUT_COLOR, s=18, zorder=4)
        _annotate_title(ax, f"cuts (scdet score, threshold={threshold:g})")

    return PanelResult(name="cuts", params=resolved_params, data=data, draw=draw)


def black_frozen_panel(
    signals: SignalBundle,
    span: tuple[float, float],
    params: Mapping[str, Any],
) -> PanelResult:
    """Compute and draw black and frozen (minus black) intervals over ``span``."""
    from sal.views.framework import PanelResult

    del params
    t0, t1 = span
    events = signals.video_events()

    frozen_clean = subtract_intervals(
        events.frozen_raw, events.black_raw, min_length=FREEZE_MIN_DURATION
    )
    black_clipped = clip_intervals(events.black_raw, t0, t1)
    frozen_clipped = clip_intervals(frozen_clean, t0, t1)

    intervals: list[dict[str, float | str]] = []
    for start, end in black_clipped:
        s_r, e_r = round(start, 3), round(end, 3)
        if s_r < e_r:
            intervals.append({"end": e_r, "kind": "black", "start": s_r})
    for start, end in frozen_clipped:
        s_r, e_r = round(start, 3), round(end, 3)
        if s_r < e_r:
            intervals.append({"end": e_r, "kind": "frozen", "start": s_r})

    intervals.sort(key=lambda item: (float(item["start"]), float(item["end"]), str(item["kind"])))

    resolved_params: dict[str, Any] = {
        "black_min_duration": BLACK_MIN_DURATION,
        "black_pix_th": BLACK_PIX_TH,
        "freeze_min_duration": FREEZE_MIN_DURATION,
        "freeze_noise": FREEZE_NOISE,
    }
    data: dict[str, Any] = {"intervals": intervals}

    black_spans = [
        (float(iv["start"]), float(iv["end"]) - float(iv["start"]))
        for iv in intervals
        if iv["kind"] == "black"
    ]
    frozen_spans = [
        (float(iv["start"]), float(iv["end"]) - float(iv["start"]))
        for iv in intervals
        if iv["kind"] == "frozen"
    ]

    def draw(ax: Axes, ruler: Ruler) -> None:
        ax.set_xlim(ruler.t0, ruler.t1)
        ax.set_ylim(0.0, 2.0)
        ax.set_yticks([0.5, 1.5])
        ax.set_yticklabels(["frozen", "black"])
        ax.axhline(1.0, color="#cbd5e1", linewidth=0.8, zorder=1)
        if black_spans:
            ax.broken_barh(
                black_spans,
                (1.15, 0.7),
                facecolors=BLACK_COLOR,
                edgecolors=BLACK_COLOR,
                linewidth=0.8,
                zorder=3,
            )
        if frozen_spans:
            ax.broken_barh(
                frozen_spans,
                (0.15, 0.7),
                facecolors=FROZEN_COLOR,
                edgecolors=FROZEN_COLOR,
                linewidth=0.8,
                alpha=0.88,
                zorder=3,
            )
        _annotate_title(ax, "black & frozen intervals (black wins over frozen)")

    return PanelResult(name="black_frozen", params=resolved_params, data=data, draw=draw)
