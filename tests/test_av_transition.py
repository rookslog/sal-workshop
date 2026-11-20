import json
import shlex
import subprocess
from importlib.resources import files
from itertools import pairwise
from pathlib import Path
from shutil import copyfile
from statistics import mean

import numpy as np
from conftest import BLUE, GREEN, RED, YELLOW, render
from jsonschema import Draft202012Validator
from media import frame_time, read_png, sha256_of

type AvView = tuple[Path, dict, str]


def panel_data(view: dict, name: str) -> dict:
    return next(panel for panel in view["panels"] if panel["name"] == name)


def assert_view_schema(view: dict) -> None:
    schema = json.loads(files("sal.schemas").joinpath("view-1.schema.json").read_text("utf-8"))
    Draft202012Validator(schema).validate(view)


def assert_tile_times(view: dict) -> None:
    from sal.views.ruler import Ruler, x_to_time

    frames = panel_data(view, "filmstrip")["data"]["frames"]
    data = view["ruler"]
    ruler = Ruler(data["x0"], data["x1"], data["t0"], data["t1"])
    assert frames
    for frame in frames:
        midpoint = frame["x"] + frame["width"] / 2
        assert abs(frame["t"] - x_to_time(midpoint, ruler)) <= 0.042
    assert all(left["t"] < right["t"] for left, right in pairwise(frames))


def assert_audio_levels(view: dict) -> None:
    waveform = panel_data(view, "waveform")["data"]
    peaks = list(zip(waveform["t"], waveform["series"]["peak"], strict=True))
    before_peaks = [peak for t, peak in peaks if t <= 5.8]
    after_peaks = [peak for t, peak in peaks if t >= 6.2]
    assert before_peaks and after_peaks
    assert all(0.085 <= peak <= 0.115 for peak in before_peaks)
    assert all(0.17 <= peak <= 0.23 for peak in after_peaks)

    loudness = panel_data(view, "loudness")["data"]
    momentary = list(zip(loudness["t"], loudness["series"]["momentary"], strict=True))
    before_levels = [value for t, value in momentary if 3.0 <= t <= 5.8 and value is not None]
    after_levels = [value for t, value in momentary if 6.8 <= t <= 11.5 and value is not None]
    assert before_levels and after_levels
    assert abs(mean(before_levels) + 20.0) <= 1.5
    assert abs(mean(after_levels) + 14.0) <= 1.5


def test_a1_process_files_schema_and_json(
    av_render: tuple[Path, subprocess.CompletedProcess[str]], av_view: AvView
) -> None:
    out_dir, process = av_render
    assert process.returncode == 0, process.stderr
    assert process.stderr == ""
    assert len(process.stdout.splitlines()) == 1
    assert json.loads(process.stdout) == {
        "png": str(out_dir / "view.png"),
        "json": str(out_dir / "view.json"),
    }
    assert (out_dir / "view.png").is_file()
    assert (out_dir / "view.json").is_file()
    _, view, text = av_view
    assert_view_schema(view)
    assert (
        text
        == json.dumps(view, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    )


def test_a2_view_and_input_metadata(av_view: AvView, av_mp4: Path) -> None:
    _, view, _ = av_view
    assert view["view"] == "av_transition"
    assert view["input"]["sha256"] == sha256_of(av_mp4)
    assert abs(view["input"]["duration"] - 12.0) <= 0.06


def test_a3_panel_order_and_boxes(av_view: AvView) -> None:
    _, view, _ = av_view
    panels = view["panels"]
    assert [panel["name"] for panel in panels] == [
        "filmstrip",
        "cuts",
        "black_frozen",
        "waveform",
        "loudness",
    ]
    ruler, image = view["ruler"], view["image"]
    for panel in panels:
        x, y, width, height = panel["box"]
        assert x == ruler["x0"]
        assert width == ruler["x1"] - ruler["x0"]
        assert 0 <= x <= x + width <= image["width"]
        assert 0 <= y <= y + height <= image["height"]
    for upper, lower in pairwise(panels):
        assert upper["box"][1] + upper["box"][3] <= lower["box"][1]


def test_a4_cut_events(av_view: AvView) -> None:
    _, view, _ = av_view
    cuts = panel_data(view, "cuts")
    events = cuts["data"]["events"]
    threshold = cuts["params"]["threshold"]
    assert isinstance(threshold, int | float) and not isinstance(threshold, bool)
    assert len(events) == 4
    for event, expected_time in zip(events, (3.0, 5.0, 6.0, 10.0), strict=True):
        assert abs(event["t"] - expected_time) <= 0.04
        assert event["score"] > 0
        assert event["score"] >= threshold


def test_a5_black_and_frozen_intervals(av_view: AvView) -> None:
    _, view, _ = av_view
    panel = panel_data(view, "black_frozen")
    intervals = panel["data"]["intervals"]
    assert len(intervals) == 2
    black, frozen = intervals
    assert black["kind"] == "black"
    assert abs(black["start"] - 5.0) <= 0.1
    assert abs(black["end"] - 6.0) <= 0.1
    assert frozen["kind"] == "frozen"
    assert abs(frozen["start"] - 8.0) <= 0.1
    assert abs(frozen["end"] - 9.0) <= 0.1
    assert black["start"] <= frozen["start"]
    assert panel["params"]["black_min_duration"] <= 0.5
    assert panel["params"]["freeze_min_duration"] <= 0.5


def test_a6_filmstrip_tiles(av_view: AvView) -> None:
    from sal.views.video import tile_layout

    _, view, _ = av_view
    filmstrip = panel_data(view, "filmstrip")
    frames = filmstrip["data"]["frames"]
    box_x, _, box_width, _ = filmstrip["box"]
    assert len(frames) >= 4
    assert [(frame["x"], frame["width"]) for frame in frames] == tile_layout(
        box_x, box_width, filmstrip["params"]["tile_width"]
    )
    assert_tile_times(view)


def test_a7_filmstrip_pixels(av_view: AvView) -> None:
    from sal.views.ruler import Ruler, x_to_time

    out_dir, view, _ = av_view
    filmstrip = panel_data(view, "filmstrip")
    _, y, _, height = filmstrip["box"]
    data = view["ruler"]
    ruler = Ruler(data["x0"], data["x1"], data["t0"], data["t1"])
    pixels = read_png(out_dir / "view.png")
    rows = [
        (0, 75, RED),
        (75, 125, GREEN),
        (125, 150, None),
        (150, 200, BLUE),
        (200, 225, BLUE),
        (225, 250, BLUE),
        (250, 300, YELLOW),
    ]
    rows_checked = set()
    for frame in filmstrip["data"]["frames"]:
        x, width = frame["x"], frame["width"]
        left = x_to_time(x, ruler)
        right = x_to_time(x + width, ruler)
        for start, end, colour in rows:
            if left >= frame_time(start, "25") + 0.1 and right <= frame_time(end, "25") - 0.1:
                centre = pixels[
                    y + height // 4 : y + height - height // 4,
                    x + width // 4 : x + width - width // 4,
                    :3,
                ]
                median = np.median(centre, axis=(0, 1))
                if colour is None:
                    assert np.all(median <= 40), (frame, median)
                else:
                    assert np.all(np.abs(median - colour) <= 45), (frame, median, colour)
                rows_checked.add(start)
    assert len(rows_checked) >= 3


def test_a8_audio_levels(av_view: AvView) -> None:
    _, view, _ = av_view
    assert_audio_levels(view)


def test_a9_commands_can_be_replayed(av_view: AvView, av_mp4: Path) -> None:
    _, view, _ = av_view
    commands = view["commands"]
    assert any("blackdetect" in command for command in commands)
    assert any("freezedetect" in command for command in commands)
    for command in commands:
        assert command.startswith(("ffmpeg ", "ffprobe "))
        assert "<input>" in command
        assert str(av_mp4) not in command
        args = shlex.split(command)
        assert shlex.join(args) == command
        args = [arg.replace("<input>", str(av_mp4)) for arg in args]
        result = subprocess.run(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False
        )
        assert result.returncode == 0, command


def test_a10_same_bytes_after_rerender_and_rename(
    av_view: AvView, av_mp4: Path, tmp_path: Path
) -> None:
    original, _, _ = av_view
    second = tmp_path / "second"
    copied = tmp_path / "renamed.mp4"
    copyfile(av_mp4, copied)
    renamed = tmp_path / "renamed-input"
    for input_path, out_dir in [(av_mp4, second), (copied, renamed)]:
        result = render("av_transition", input_path, out_dir)
        assert result.returncode == 0, result.stderr
        for name in ("view.png", "view.json"):
            assert (original / name).read_bytes() == (out_dir / name).read_bytes()
