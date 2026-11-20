import json
import shlex
import subprocess
from collections.abc import Iterator
from importlib.resources import files
from itertools import pairwise
from pathlib import Path
from statistics import mean, median

import pytest
from conftest import load_view, render, run_sal
from jsonschema import Draft202012Validator
from media import read_png, sha256_of


@pytest.fixture(scope="module")
def step_render(
    tmp_path_factory: pytest.TempPathFactory, step_wav: Path
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    out_dir = tmp_path_factory.mktemp("step-render") / "out"
    return out_dir, render("audio_basic", step_wav, out_dir)


@pytest.fixture(scope="module")
def step_view(step_render: tuple[Path, subprocess.CompletedProcess[str]]) -> tuple[Path, dict, str]:
    out_dir, process = step_render
    assert process.returncode == 0, process.stderr
    text, view = load_view(out_dir)
    return out_dir, view, text


def test_v1_render_process(step_render: tuple[Path, subprocess.CompletedProcess[str]]) -> None:
    out_dir, process = step_render
    assert process.returncode == 0, process.stderr
    assert process.stderr == ""
    assert len(process.stdout.splitlines()) == 1
    assert json.loads(process.stdout) == {
        "png": str(out_dir / "view.png"),
        "json": str(out_dir / "view.json"),
    }
    assert (out_dir / "view.png").is_file()
    assert (out_dir / "view.json").is_file()


def test_v2_view_schema(step_view: tuple[Path, dict, str]) -> None:
    _, view, _ = step_view
    schema = json.loads(files("sal.schemas").joinpath("view-1.schema.json").read_text("utf-8"))
    Draft202012Validator(schema).validate(view)


def test_v3_canonical_json(step_view: tuple[Path, dict, str]) -> None:
    _, view, text = step_view
    assert (
        text
        == json.dumps(view, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    )


def test_v4_metadata(step_view: tuple[Path, dict, str], step_wav: Path) -> None:
    _, view, _ = step_view
    assert view["schema"] == "sal.view/1"
    assert view["view"] == "audio_basic"
    assert view["input"]["sha256"] == sha256_of(step_wav)
    assert abs(view["input"]["duration"] - 10.0) <= 0.01


def test_v5_span_and_ruler(step_view: tuple[Path, dict, str]) -> None:
    _, view, _ = step_view
    span, ruler = view["span"], view["ruler"]
    assert span == {"start": 0.0, "end": view["input"]["duration"]}
    assert ruler["t0"] == span["start"]
    assert ruler["t1"] == span["end"]
    assert 0 <= ruler["x0"] < ruler["x1"] <= view["image"]["width"]


def test_v6_image_metadata(step_view: tuple[Path, dict, str]) -> None:
    out_dir, view, _ = step_view
    image = view["image"]
    assert image["file"] == "view.png"
    height, width = read_png(out_dir / "view.png").shape[:2]
    assert (image["width"], image["height"]) == (width, height)


def test_v7_panel_boxes(step_view: tuple[Path, dict, str]) -> None:
    _, view, _ = step_view
    panels = view["panels"]
    assert [panel["name"] for panel in panels] == ["waveform", "loudness"]
    ruler, image = view["ruler"], view["image"]
    for panel in panels:
        x, y, width, height = panel["box"]
        assert x == ruler["x0"]
        assert width == ruler["x1"] - ruler["x0"]
        assert 0 <= x <= x + width <= image["width"]
        assert 0 <= y <= y + height <= image["height"]
    _, wave_y, _, wave_height = panels[0]["box"]
    assert wave_y + wave_height <= panels[1]["box"][1]


def test_v8_ruler_ticks(step_view: tuple[Path, dict, str]) -> None:
    from sal.views.ruler import Ruler, time_to_x

    _, view, _ = step_view
    data = view["ruler"]
    ruler = Ruler(data["x0"], data["x1"], data["t0"], data["t1"])
    ticks = data["ticks"]
    assert len(ticks) >= 3
    differences = [right["t"] - left["t"] for left, right in pairwise(ticks)]
    assert all(difference > 0 for difference in differences)
    assert max(differences) - min(differences) <= 0.001
    for tick in ticks:
        assert ruler.t0 <= tick["t"] <= ruler.t1
        assert abs(tick["x"] - time_to_x(tick["t"], ruler)) <= 0.5
        assert isinstance(tick["label"], str) and tick["label"]


def _time_values(node: object, under_time_key: bool = False) -> Iterator[int | float]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _time_values(
                value, under_time_key or key in {"t", "t0", "t1", "start", "end", "duration"}
            )
    elif isinstance(node, list):
        for value in node:
            yield from _time_values(value, under_time_key)
    elif type(node) in (int, float) and under_time_key:
        yield node


def test_v9_times_have_millisecond_precision(step_view: tuple[Path, dict, str]) -> None:
    _, view, _ = step_view
    for value in _time_values(view):
        assert round(value, 3) == value


def _panel(view: dict, name: str) -> dict:
    return next(panel for panel in view["panels"] if panel["name"] == name)


def test_v10_waveform_levels(step_view: tuple[Path, dict, str]) -> None:
    _, view, _ = step_view
    panel = _panel(view, "waveform")
    times, peaks = panel["data"]["t"], panel["data"]["series"]["peak"]
    assert len(times) == len(peaks) == panel["params"]["bins"] >= 200
    assert all(left < right for left, right in pairwise(times))
    assert times[0] > 0
    assert times[-1] < 10
    before = [peak for t, peak in zip(times, peaks, strict=True) if t <= 4.9]
    after = [peak for t, peak in zip(times, peaks, strict=True) if t >= 5.1]
    assert before and after
    assert all(0.098 <= peak <= 0.102 for peak in before)
    assert all(0.196 <= peak <= 0.204 for peak in after)
    assert abs(median(after) / median(before) - 2.0) <= 0.05


def test_v11_momentary_loudness(step_view: tuple[Path, dict, str]) -> None:
    _, view, _ = step_view
    data = _panel(view, "loudness")["data"]
    times = data["t"]
    momentary = data["series"]["momentary"]
    short_term = data["series"]["short_term"]
    assert len(times) == len(momentary) == len(short_term) >= 20
    assert all(left < right for left, right in pairwise(times))
    assert 0 <= times[0] <= times[-1] <= 10.5
    early = [
        level for t, level in zip(times, momentary, strict=True) if t <= 4.9 and level is not None
    ]
    before = [
        level
        for t, level in zip(times, momentary, strict=True)
        if 3.0 <= t <= 4.9 and level is not None
    ]
    after = [
        level
        for t, level in zip(times, momentary, strict=True)
        if 5.6 <= t <= 9.5 and level is not None
    ]
    assert before and after
    assert abs(mean(before) - (-20.0)) <= 1.0
    assert abs(mean(after) - (-14.0)) <= 1.0
    assert abs(mean(after) - mean(before) - 6.0) <= 1.0
    assert all(level <= -19.0 for level in early)


def test_v12_short_term_loudness(step_view: tuple[Path, dict, str]) -> None:
    _, view, _ = step_view
    data = _panel(view, "loudness")["data"]
    short_term = data["series"]["short_term"]
    assert any(level is not None for level in short_term)
    late = [
        level
        for t, level in zip(data["t"], short_term, strict=True)
        if 8.5 <= t <= 9.5 and level is not None
    ]
    assert late
    assert abs(mean(late) - (-14.0)) <= 1.0


def test_v13_loudness_scale(step_view: tuple[Path, dict, str]) -> None:
    _, view, _ = step_view
    params = _panel(view, "loudness")["params"]
    y_min, y_max = params["y_min"], params["y_max"]
    assert type(y_min) in (int, float) and type(y_max) in (int, float)
    assert y_min < y_max
    assert y_min <= -40
    assert y_max >= -5


def test_v14_tool_versions(step_view: tuple[Path, dict, str]) -> None:
    import sal

    _, view, _ = step_view
    doctor = run_sal("doctor", "--json")
    assert doctor.returncode == 0, doctor.stderr
    assert view["tool"]["sal"] == sal.__version__
    assert view["tool"]["ffmpeg"] == json.loads(doctor.stdout)["ffmpeg"]


def test_v15_command_placeholders(step_view: tuple[Path, dict, str], step_wav: Path) -> None:
    _, view, _ = step_view
    commands = view["commands"]
    assert commands
    for command in commands:
        assert command.startswith(("ffmpeg ", "ffprobe "))
        assert "<input>" in command
        assert str(step_wav.parent) not in command
        assert step_wav.name not in command


def test_v16_commands_can_be_replayed(step_view: tuple[Path, dict, str], step_wav: Path) -> None:
    _, view, _ = step_view
    for command in view["commands"]:
        args = [arg.replace("<input>", str(step_wav)) for arg in shlex.split(command)]
        result = subprocess.run(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False
        )
        assert result.returncode == 0, command
