import subprocess
from pathlib import Path

from conftest import load_view, render
from media import frame_time
from test_av_transition import (
    assert_audio_levels,
    assert_tile_times,
    assert_view_schema,
    panel_data,
)
from test_view_errors import _assert_refused
from test_view_errors import refusal_output as refusal_output


def _render_av(input_path: Path, out_dir: Path, span: str | None = None) -> dict:
    result = render("av_transition", input_path, out_dir, span)
    assert result.returncode == 0, result.stderr
    _, view = load_view(out_dir)
    return view


def test_n1_events_intervals_and_tiles_inside_span(av_mp4: Path, tmp_path: Path) -> None:
    view = _render_av(av_mp4, tmp_path / "out", "4:7")
    events = panel_data(view, "cuts")["data"]["events"]
    assert len(events) == 2
    assert 4.96 <= events[0]["t"] <= 5.04
    assert 5.96 <= events[1]["t"] <= 6.04

    intervals = panel_data(view, "black_frozen")["data"]["intervals"]
    assert len(intervals) == 1
    black = intervals[0]
    assert black["kind"] == "black"
    assert abs(black["start"] - 5.0) <= 0.1
    assert abs(black["end"] - 6.0) <= 0.1

    frames = panel_data(view, "filmstrip")["data"]["frames"]
    assert frames
    assert all(4.0 <= frame["t"] <= 7.0 for frame in frames)
    assert view["ruler"]["t0"] == 4.0
    assert view["ruler"]["t1"] == 7.0


def test_n2_intervals_are_clipped_at_span_edges(av_mp4: Path, tmp_path: Path) -> None:
    view = _render_av(av_mp4, tmp_path / "out", "5.5:8.5")
    events = panel_data(view, "cuts")["data"]["events"]
    assert len(events) == 1
    assert 5.96 <= events[0]["t"] <= 6.04

    intervals = panel_data(view, "black_frozen")["data"]["intervals"]
    assert len(intervals) == 2
    black, frozen = intervals
    assert black["kind"] == "black"
    assert black["start"] == 5.5
    assert abs(black["end"] - 6.0) <= 0.1
    assert frozen["kind"] == "frozen"
    assert abs(frozen["start"] - 8.0) <= 0.1
    assert frozen["end"] == 8.5


def test_n3_moving_only_span_has_no_events_or_intervals(av_mp4: Path, tmp_path: Path) -> None:
    view = _render_av(av_mp4, tmp_path / "out", "6.5:7.5")
    assert panel_data(view, "cuts")["data"]["events"] == []
    assert panel_data(view, "black_frozen")["data"]["intervals"] == []
    assert_view_schema(view)


def test_n4_fractional_frame_rate(ntsc_mp4: Path, tmp_path: Path) -> None:
    view = _render_av(ntsc_mp4, tmp_path / "out")
    events = panel_data(view, "cuts")["data"]["events"]
    assert len(events) == 1
    cut_time = frame_time(48, "24000/1001")
    assert round(cut_time - 0.042, 3) <= events[0]["t"] <= round(cut_time + 0.042, 3)
    assert panel_data(view, "black_frozen")["data"]["intervals"] == []
    assert abs(view["input"]["duration"] - 4.004) <= 0.06
    assert_tile_times(view)


def test_n5_audio_basic_reads_audio_from_video(av_mp4: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    result = render("audio_basic", av_mp4, out_dir)
    assert result.returncode == 0, result.stderr
    _, view = load_view(out_dir)
    assert [panel["name"] for panel in view["panels"]] == ["waveform", "loudness"]
    assert_audio_levels(view)


def test_n6_missing_video_stream(step_wav: Path, refusal_output: tuple[Path, bool]) -> None:
    out_dir = refusal_output[0]
    result = render("av_transition", step_wav, out_dir)
    _assert_refused(result, refusal_output)
    # An argument echo is not a missing-stream diagnostic.
    diagnostic = result.stderr.replace(str(out_dir), "").replace(str(step_wav), "")
    assert "video" in diagnostic.lower(), result.stderr


def test_n7_missing_audio_stream(tmp_path: Path, refusal_output: tuple[Path, bool]) -> None:
    video = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x48:d=1",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        timeout=120,
    )
    out_dir = refusal_output[0]
    result = render("av_transition", video, out_dir)
    _assert_refused(result, refusal_output)
    diagnostic = result.stderr.replace(str(out_dir), "").replace(str(video), "")
    assert "audio" in diagnostic.lower(), result.stderr
