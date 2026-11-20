import json
import subprocess
from math import ceil, floor
from pathlib import Path

import numpy as np
import pytest
from conftest import load_view, make_round, read_json, render
from media import make_wav, read_png


def _ffmpeg(*args: str) -> None:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def offset_audio_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("offset-audio") / "offset.mov"
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=96x64:rate=25:duration=6",
        "-itsoffset",
        "2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=48000:duration=2",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "pcm_s16le",
        str(path),
    )
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,start_time,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    streams = {stream["codec_type"]: stream for stream in json.loads(result.stdout)["streams"]}
    assert float(streams["audio"]["start_time"]) == pytest.approx(2.0, abs=0.05), streams
    assert float(streams["audio"]["duration"]) == pytest.approx(2.0, abs=0.05), streams
    assert float(streams["video"]["start_time"]) == 0.0, streams
    assert float(streams["video"]["duration"]) == 6.0, streams
    return path


def _panel(view: dict, name: str) -> dict:
    return next(panel for panel in view["panels"] if panel["name"] == name)


def _waveform_columns(out: Path, silent_out: Path, start: float, end: float) -> np.ndarray:
    from sal.views.ruler import Ruler, time_to_x

    _, view = load_view(out)
    _, silent_view = load_view(silent_out)
    assert view["ruler"] == silent_view["ruler"]
    box = _panel(view, "waveform")["box"]
    assert _panel(silent_view, "waveform")["box"] == box
    _, y, _, height = box
    ruler = Ruler(**{key: view["ruler"][key] for key in ("x0", "x1", "t0", "t1")})
    left = ceil(time_to_x(start, ruler))
    right = floor(time_to_x(end, ruler))
    assert left < right
    chart = read_png(out / "view.png")[y : y + height, left:right]
    reference = read_png(silent_out / "view.png")[y : y + height, left:right]
    return np.any(chart != reference, axis=(0, 2))


def _round_gains(root: Path, first: Path, second: Path) -> tuple[float, float]:
    round_dir = make_round(root, (first, second))
    quiet, loud = read_json(round_dir / "build" / "page.json")["takes"]
    return quiet["gain_db"], loud["gain_db"]


def test_v1_render_replaces_output_symlinks(tmp_path: Path, step_wav: Path) -> None:
    outcomes = {}
    for name in ("view.png", "view.json"):
        case_dir = tmp_path / name
        out = case_dir / "out"
        out.mkdir(parents=True)
        precious = case_dir / "precious.bin"
        original = b"keep these bytes\n"
        precious.write_bytes(original)
        link = out / name
        link.symlink_to(precious)

        result = render("audio_basic", step_wav, out)

        assert result.returncode == 0, result.stderr
        outcomes[name] = (precious.read_bytes() == original, link.is_file(), link.is_symlink())
        assert (out / "view.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert load_view(out)[1]["schema"] == "sal.view/1"

    assert outcomes == {"view.png": (True, True, False), "view.json": (True, True, False)}


def test_v2_failed_second_output_keeps_png(
    tmp_path: Path, step_wav: Path, silent_wav: Path
) -> None:
    out = tmp_path / "out"
    result = render("audio_basic", step_wav, out)
    assert result.returncode == 0, result.stderr
    png = out / "view.png"
    original = png.read_bytes()
    json_path = out / "view.json"
    json_path.unlink()
    json_path.mkdir()

    # Use different media so rewriting the PNG cannot look like leaving it alone.
    result = render("audio_basic", silent_wav, out)

    assert result.returncode != 0
    assert png.read_bytes() == original


def test_v3_waveform_keeps_audio_offset(
    tmp_path: Path, offset_audio_clip: Path, silent_wav: Path
) -> None:
    out = tmp_path / "offset"
    baseline = tmp_path / "silence"
    result = render("audio_basic", offset_audio_clip, out)
    assert result.returncode == 0, result.stderr
    result = render("audio_basic", silent_wav, baseline, "0:6")
    assert result.returncode == 0, result.stderr

    before = _waveform_columns(out, baseline, 0.2, 1.8)
    during = _waveform_columns(out, baseline, 2.2, 3.8)
    after = _waveform_columns(out, baseline, 4.2, 5.8)
    assert (int(before.sum()), int(after.sum())) == (0, 0)
    assert during.all()


def test_v4_crop_contains_offset_audio(
    tmp_path: Path, offset_audio_clip: Path, silent_wav: Path
) -> None:
    out = tmp_path / "offset"
    baseline = tmp_path / "silence"
    result = render("audio_basic", offset_audio_clip, out, "2:4")
    assert result.returncode == 0, result.stderr
    result = render("audio_basic", silent_wav, baseline, "2:4")
    assert result.returncode == 0, result.stderr

    assert _waveform_columns(out, baseline, 2.01, 3.99).all()


def test_v5_silence_does_not_set_matching_level(tmp_path: Path) -> None:
    silence = make_wav(tmp_path / "silence.wav", [(3.0, 0, 0.0)])
    tone = make_wav(tmp_path / "tone.wav", [(3.0, 1000, 0.1)])

    assert _round_gains(tmp_path, silence, tone) == (0.0, 0.0)


def test_v6_louder_tone_is_attenuated(tmp_path: Path) -> None:
    quiet = make_wav(tmp_path / "quiet.wav", [(3.0, 1000, 0.1)])
    loud = make_wav(tmp_path / "loud.wav", [(3.0, 1000, 0.2)])

    quiet_gain, loud_gain = _round_gains(tmp_path, quiet, loud)

    assert quiet_gain == 0.0
    assert loud_gain == pytest.approx(-6.0, abs=0.3)


def test_v7_black_flash_is_below_minimum_duration(tmp_path: Path) -> None:
    path = tmp_path / "flash.mp4"
    # Half a second is between frame boundaries at 25 fps; use 13 frames (0.52 s).
    picture = (
        "color=black:s=96x64:r=25:d=0.08[a];color=white:s=96x64:r=25:d=2[b];"
        "color=black:s=96x64:r=25:d=0.52[c];color=white:s=96x64:r=25:d=1[d];"
        "[a][b][c][d]concat=n=4:v=1:a=0"
    )
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        picture,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=48000:duration=3.6",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(path),
    )
    out = tmp_path / "out"
    result = render("av_transition", path, out)
    assert result.returncode == 0, result.stderr
    _, view = load_view(out)
    panel = _panel(view, "black_frozen")
    black = [entry for entry in panel["data"]["intervals"] if entry["kind"] == "black"]

    assert len(black) == 1, black
    duration = black[0]["end"] - black[0]["start"]
    assert duration == pytest.approx(0.5, abs=0.09)
    assert duration >= panel["params"]["black_min_duration"]


def test_v8_end_span_shows_last_frame(tmp_path: Path) -> None:
    path = tmp_path / "one-second.mp4"
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=96x64:rate=25:duration=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=48000:duration=1",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(path),
    )
    out = tmp_path / "out"

    result = render("av_transition", path, out, "0.98:1")

    assert result.returncode == 0, result.stderr
    assert (out / "view.png").is_file()
    assert (out / "view.json").is_file()
