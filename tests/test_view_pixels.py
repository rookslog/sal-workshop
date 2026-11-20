import json
from importlib.resources import files
from math import ceil, floor
from pathlib import Path

import numpy as np
import pytest
from conftest import load_view, render
from jsonschema import Draft202012Validator
from media import make_wav, read_png


def _render_view(input_path: Path, out_dir: Path, span: str | None = None) -> tuple[Path, dict]:
    result = render("audio_basic", input_path, out_dir, span)
    assert result.returncode == 0, result.stderr
    _, view = load_view(out_dir)
    return out_dir, view


@pytest.fixture(scope="module")
def whole_silence(tmp_path_factory: pytest.TempPathFactory, silent_wav: Path) -> tuple[Path, dict]:
    return _render_view(silent_wav, tmp_path_factory.mktemp("whole-silence") / "out")


@pytest.fixture(scope="module")
def whole_burst(tmp_path_factory: pytest.TempPathFactory, burst_wav: Path) -> tuple[Path, dict]:
    return _render_view(burst_wav, tmp_path_factory.mktemp("whole-burst") / "out")


@pytest.fixture(scope="module")
def cropped_burst(tmp_path_factory: pytest.TempPathFactory, burst_wav: Path) -> tuple[Path, dict]:
    return _render_view(burst_wav, tmp_path_factory.mktemp("cropped-burst") / "out", "2:6")


def _panel(view: dict, name: str) -> dict:
    return next(panel for panel in view["panels"] if panel["name"] == name)


def _assert_burst_pixels(silence: tuple[Path, dict], burst: tuple[Path, dict]) -> None:
    from sal.views.ruler import Ruler, time_to_x

    silence_dir, silence_view = silence
    burst_dir, burst_view = burst
    silence_pixels = read_png(silence_dir / "view.png")
    burst_pixels = read_png(burst_dir / "view.png")
    box = _panel(burst_view, "waveform")["box"]
    assert _panel(silence_view, "waveform")["box"] == box
    x, y, width, height = box
    silence_box = silence_pixels[y : y + height, x : x + width]
    burst_box = burst_pixels[y : y + height, x : x + width]
    columns = np.flatnonzero(np.any(silence_box != burst_box, axis=(0, 2))) + x
    assert columns.size > 0
    first, last = columns[0], columns[-1]
    data = burst_view["ruler"]
    ruler = Ruler(data["x0"], data["x1"], data["t0"], data["t1"])
    assert first >= floor(time_to_x(3.000, ruler)) - 2
    assert last <= ceil(time_to_x(3.010, ruler)) + 2
    assert abs((first + last) / 2 - time_to_x(3.005, ruler)) <= 1.5


def test_x1_burst_pixels_whole_file(
    whole_silence: tuple[Path, dict], whole_burst: tuple[Path, dict]
) -> None:
    _assert_burst_pixels(whole_silence, whole_burst)


def test_x2_burst_pixels_cropped_span(
    tmp_path: Path, silent_wav: Path, cropped_burst: tuple[Path, dict]
) -> None:
    cropped_silence = _render_view(silent_wav, tmp_path / "cropped-silence", "2:6")
    _, burst_view = cropped_burst
    assert burst_view["ruler"]["t0"] == 2.0
    assert burst_view["ruler"]["t1"] == 6.0
    _assert_burst_pixels(cropped_silence, cropped_burst)


def _assert_burst_bins(view: dict) -> None:
    waveform = _panel(view, "waveform")
    times = waveform["data"]["t"]
    peaks = waveform["data"]["series"]["peak"]
    nearest = min(range(len(times)), key=lambda index: abs(times[index] - 3.005))
    assert 0.45 <= peaks[nearest] <= 0.51
    span = view["span"]
    bin_width = (span["end"] - span["start"]) / waveform["params"]["bins"]
    for time, peak in zip(times, peaks, strict=True):
        if abs(time - 3.005) > 2 * bin_width:
            assert peak <= 0.001


def test_x3_burst_bins_whole_file(whole_burst: tuple[Path, dict]) -> None:
    _, view = whole_burst
    _assert_burst_bins(view)


def test_x4_burst_bins_cropped_span(cropped_burst: tuple[Path, dict]) -> None:
    _, view = cropped_burst
    assert view["span"] == {"start": 2.0, "end": 6.0}
    assert all(2.0 <= time <= 6.0 for time in _panel(view, "waveform")["data"]["t"])
    _assert_burst_bins(view)
    assert all(2.0 <= tick["t"] <= 6.0 for tick in view["ruler"]["ticks"])


def test_x5_silent_signal(whole_silence: tuple[Path, dict]) -> None:
    _, view = whole_silence
    peaks = _panel(view, "waveform")["data"]["series"]["peak"]
    momentary = _panel(view, "loudness")["data"]["series"]["momentary"]
    assert all(peak <= 0.001 for peak in peaks)
    assert all(level is None or level < -60 for level in momentary)


def test_x6_right_channel_only(tmp_path: Path) -> None:
    input_path = make_wav(tmp_path / "right.wav", [(4.0, 1000, 0.3)], gains=[0.0, 1.0])
    _, view = _render_view(input_path, tmp_path / "out")
    peaks = _panel(view, "waveform")["data"]["series"]["peak"]
    assert peaks
    assert all(abs(peak - 0.3) <= 0.006 for peak in peaks)


def test_x7_mono_44100(tmp_path: Path) -> None:
    input_path = make_wav(tmp_path / "mono.wav", [(4.0, 440, 0.25)], rate=44100, channels=1)
    _, view = _render_view(input_path, tmp_path / "out")
    peaks = _panel(view, "waveform")["data"]["series"]["peak"]
    assert peaks
    assert all(abs(peak - 0.25) <= 0.006 for peak in peaks)
    assert abs(view["input"]["duration"] - 4.0) <= 0.01
    schema = json.loads(files("sal.schemas").joinpath("view-1.schema.json").read_text("utf-8"))
    Draft202012Validator(schema).validate(view)
