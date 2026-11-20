"""Audio decoding, peak binning, loudness measurement, and audio panels."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import numpy as np

from sal.views.media import INPUT_PLACEHOLDER
from sal.views.ruler import Ruler

if TYPE_CHECKING:
    from matplotlib.axes import Axes

    from sal.views.framework import PanelResult, SignalBundle

WAVEFORM_COLOR = "#2b6cb0"
WAVEFORM_ZERO_COLOR = "#bbbbbb"
MOMENTARY_COLOR = "#0f766e"
SHORT_TERM_COLOR = "#d97706"
DEFAULT_WAVEFORM_BINS = 1000
MIN_WAVEFORM_BINS = 200
LOUDNESS_Y_MIN = -60.0
LOUDNESS_Y_MAX = 0.0
LOUDNESS_SILENCE_FLOOR = -120.0


def peak_bins(samples: np.ndarray, bins: int) -> np.ndarray:
    """Compute per-bin maximum absolute sample across all channels.

    ``samples`` has shape ``(n,)`` or ``(n, channels)``. Bin ``i`` covers sample
    rows ``[floor(i * n / bins), floor((i + 1) * n / bins))``.
    """
    if isinstance(bins, bool) or not isinstance(bins, int | np.integer):
        raise ValueError("bins must be an integer")
    bins_int = int(bins)

    arr = np.asarray(samples, dtype=np.float64)
    if arr.ndim not in (1, 2):
        raise ValueError(f"samples must be 1D or 2D, got shape {arr.shape}")

    n = arr.shape[0]
    if bins_int < 1 or bins_int > n:
        raise ValueError(f"bins ({bins_int}) must be between 1 and sample count ({n})")

    row_max = np.abs(arr) if arr.ndim == 1 else np.max(np.abs(arr), axis=1)
    edges = (np.arange(bins_int + 1, dtype=np.int64) * n) // bins_int
    result: np.ndarray = np.maximum.reduceat(row_max, edges[:-1])
    return result


def decode_audio_pcm(signals: SignalBundle) -> np.ndarray:
    """Decode the first audio stream as float32 samples shaped ``(n, channels)``."""
    if not signals.media.has_audio or signals.media.channels is None:
        raise ValueError("input file has no audio stream")

    template = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        INPUT_PLACEHOLDER,
        "-map",
        "0:a:0",
        "-acodec",
        "pcm_f32le",
        "-f",
        "f32le",
        "pipe:1",
    ]
    result = signals.recorder.run(template, text=False)
    raw = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode("latin-1")
    channels = signals.media.channels
    frame_bytes = 4 * channels
    usable_bytes = (len(raw) // frame_bytes) * frame_bytes
    if usable_bytes == 0:
        raise ValueError("decoded audio stream is empty")
    samples = np.frombuffer(raw[:usable_bytes], dtype="<f4").reshape(-1, channels)
    return samples


def _clean_lufs(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value) or value <= LOUDNESS_SILENCE_FLOOR:
        return None
    return round(value, 3)


def measure_ebur128(
    signals: SignalBundle,
) -> tuple[list[float], list[float | None], list[float | None]]:
    """Measure momentary (400 ms) and short-term (3 s) EBU R 128 loudness via ffmpeg."""
    if not signals.media.has_audio:
        raise ValueError("input file has no audio stream")

    template = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        INPUT_PLACEHOLDER,
        "-map",
        "0:a:0",
        "-af",
        "ebur128=metadata=1,ametadata=mode=print:file=-",
        "-f",
        "null",
        "-",
    ]
    result = signals.recorder.run(template, text=True)
    stdout = result.stdout if isinstance(result.stdout, str) else result.stdout.decode("utf-8")

    times: list[float] = []
    momentary: list[float | None] = []
    short_term: list[float | None] = []

    current_t: float | None = None
    current_m: str | None = None
    current_s: str | None = None

    def flush() -> None:
        if current_t is None:
            return
        if not times or current_t > times[-1]:
            times.append(current_t)
            momentary.append(_clean_lufs(current_m))
            short_term.append(_clean_lufs(current_s))

    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("frame:"):
            flush()
            current_t = None
            current_m = None
            current_s = None
            for token in line.split():
                if token.startswith("pts_time:"):
                    try:
                        parsed_t = float(token.split(":", 1)[1])
                    except ValueError:
                        parsed_t = math.nan
                    if math.isfinite(parsed_t) and parsed_t >= 0.0:
                        current_t = round(parsed_t, 3)
        elif line.startswith("lavfi.r128.M="):
            current_m = line.split("=", 1)[1]
        elif line.startswith("lavfi.r128.S="):
            current_s = line.split("=", 1)[1]

    flush()
    if not times:
        raise ValueError("ffmpeg ebur128 produced no loudness measurements")
    return times, momentary, short_term


def _choose_waveform_bins(span_duration: float, sample_count: int, requested: int) -> int:
    # Keep bin centers at least 2 ms apart so 3-decimal timestamps stay strictly increasing.
    max_distinct_ms_bins = max(MIN_WAVEFORM_BINS, int(math.floor(span_duration / 0.002)))
    bins = min(requested, max_distinct_ms_bins, sample_count)
    if bins < MIN_WAVEFORM_BINS:
        raise ValueError(
            f"span too short ({span_duration:.3f}s, {sample_count} samples) for "
            f"{MIN_WAVEFORM_BINS} waveform bins"
        )
    return bins


def waveform_panel(
    signals: SignalBundle,
    span: tuple[float, float],
    params: Mapping[str, Any],
) -> PanelResult:
    """Compute and draw the peak waveform panel over ``span``."""
    from sal.views.framework import PanelResult

    t0, t1 = span
    if signals.media.sample_rate is None:
        raise ValueError("input file has no audio stream")

    samples = signals.audio_samples()
    rate = signals.media.sample_rate
    total_frames = samples.shape[0]
    start_idx = max(0, min(total_frames, round(t0 * rate)))
    end_idx = max(start_idx, min(total_frames, round(t1 * rate)))
    sliced = samples[start_idx:end_idx]
    if sliced.shape[0] == 0:
        raise ValueError("selected span contains no audio samples")

    requested_bins = int(params.get("bins", DEFAULT_WAVEFORM_BINS))
    bins = _choose_waveform_bins(t1 - t0, sliced.shape[0], requested_bins)

    peaks_arr = np.clip(peak_bins(sliced, bins), 0.0, 1.0)
    bin_width = (t1 - t0) / bins
    t_mid = [round(t0 + (i + 0.5) * bin_width, 3) for i in range(bins)]
    peaks = [round(float(v), 6) for v in peaks_arr]

    resolved_params: dict[str, Any] = {
        "bins": bins,
        "y_max": 1.0,
        "y_min": -1.0,
    }
    data: dict[str, Any] = {
        "series": {"peak": peaks},
        "t": t_mid,
    }

    t_edges = np.linspace(t0, t1, bins + 1)

    def draw(ax: Axes, ruler: Ruler) -> None:
        ax.set_xlim(ruler.t0, ruler.t1)
        ax.set_ylim(-1.0, 1.0)
        ax.set_yticks([-1.0, -0.5, 0.0, 0.5, 1.0])
        ax.set_ylabel("Amplitude", fontsize=9, color="#222222")
        ax.axhline(0.0, color=WAVEFORM_ZERO_COLOR, linewidth=0.7, zorder=1)
        mask = peaks_arr > 0.0
        if np.any(mask):
            # Draw each contiguous non-zero run as one polygon to avoid inter-bin hairline gaps.
            padded = np.concatenate(([False], mask, [False]))
            starts = np.flatnonzero(~padded[:-1] & padded[1:])
            ends = np.flatnonzero(padded[:-1] & ~padded[1:])
            for run_start, run_end in zip(starts, ends, strict=True):
                run_edges = t_edges[run_start : run_end + 1]
                run_peaks = peaks_arr[run_start:run_end]
                xs = np.repeat(run_edges, 2)[1:-1]
                ys = np.repeat(run_peaks, 2)
                ax.fill_between(
                    xs,
                    -ys,
                    ys,
                    color=WAVEFORM_COLOR,
                    linewidth=0,
                    antialiased=False,
                    zorder=2,
                )
        ax.text(
            0.0,
            1.04,
            "waveform (peak amplitude, full scale ±1.0)",
            transform=ax.transAxes,
            ha="left",
            va="bottom",
            fontsize=10,
            fontweight="bold",
            color="#111111",
            clip_on=False,
        )

    return PanelResult(name="waveform", params=resolved_params, data=data, draw=draw)


def loudness_panel(
    signals: SignalBundle,
    span: tuple[float, float],
    params: Mapping[str, Any],
) -> PanelResult:
    """Compute and draw the EBU R 128 momentary and short-term loudness panel."""
    from sal.views.framework import PanelResult

    t0, t1 = span
    y_min = float(params.get("y_min", LOUDNESS_Y_MIN))
    y_max = float(params.get("y_max", LOUDNESS_Y_MAX))

    all_t, all_m, all_s = signals.ebur128()
    times: list[float] = []
    momentary: list[float | None] = []
    short_term: list[float | None] = []
    for t_val, m_val, s_val in zip(all_t, all_m, all_s, strict=True):
        if t0 <= t_val <= t1:
            times.append(t_val)
            momentary.append(m_val)
            short_term.append(s_val)

    resolved_params: dict[str, Any] = {
        "momentary_window_s": 0.4,
        "short_term_window_s": 3.0,
        "y_max": y_max,
        "y_min": y_min,
    }
    data: dict[str, Any] = {
        "series": {
            "momentary": momentary,
            "short_term": short_term,
        },
        "t": times,
    }

    t_arr = np.asarray(times, dtype=np.float64)
    m_arr = np.asarray([np.nan if v is None else v for v in momentary], dtype=np.float64)
    s_arr = np.asarray([np.nan if v is None else v for v in short_term], dtype=np.float64)

    def draw(ax: Axes, ruler: Ruler) -> None:
        ax.set_xlim(ruler.t0, ruler.t1)
        ax.set_ylim(y_min, y_max)
        ax.set_yticks([-60.0, -50.0, -40.0, -30.0, -20.0, -10.0, 0.0])
        ax.set_ylabel("LUFS", fontsize=9, color="#222222")
        if t_arr.size > 0:
            line_m = ax.plot(
                t_arr,
                m_arr,
                color=MOMENTARY_COLOR,
                linewidth=1.3,
                label="momentary (400 ms)",
                zorder=2,
            )[0]
            line_s = ax.plot(
                t_arr,
                s_arr,
                color=SHORT_TERM_COLOR,
                linewidth=2.0,
                label="short-term (3 s)",
                zorder=3,
            )[0]
            ax.legend(
                handles=[line_m, line_s],
                loc="lower right",
                bbox_to_anchor=(1.0, 1.02),
                ncol=2,
                frameon=False,
                fontsize=8.5,
                borderaxespad=0.0,
            )
        ax.text(
            0.0,
            1.04,
            "loudness (EBU R 128)",
            transform=ax.transAxes,
            ha="left",
            va="bottom",
            fontsize=10,
            fontweight="bold",
            color="#111111",
            clip_on=False,
        )

    return PanelResult(name="loudness", params=resolved_params, data=data, draw=draw)
