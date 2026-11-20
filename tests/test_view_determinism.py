from pathlib import Path
from shutil import copyfile

import pytest
from conftest import load_view, render
from media import make_wav


def _render_bytes(input_path: Path, out_dir: Path, span: str | None = None) -> tuple[bytes, bytes]:
    result = render("audio_basic", input_path, out_dir, span)
    assert result.returncode == 0, result.stderr
    return (out_dir / "view.png").read_bytes(), (out_dir / "view.json").read_bytes()


@pytest.fixture(scope="module")
def first_render(
    tmp_path_factory: pytest.TempPathFactory, step_wav: Path
) -> tuple[Path, bytes, bytes]:
    out_dir = tmp_path_factory.mktemp("first-render") / "out"
    png, json = _render_bytes(step_wav, out_dir)
    return out_dir, png, json


def test_d1_repeated_render(
    first_render: tuple[Path, bytes, bytes], step_wav: Path, tmp_path: Path
) -> None:
    _, png, json = first_render
    assert _render_bytes(step_wav, tmp_path / "second") == (png, json)


def test_d2_renamed_input(
    first_render: tuple[Path, bytes, bytes], step_wav: Path, tmp_path: Path
) -> None:
    _, png, json = first_render
    copied = tmp_path / "renamed.wav"
    copyfile(step_wav, copied)
    assert _render_bytes(copied, tmp_path / "copied-input") == (png, json)


def test_d3_explicit_whole_span(
    first_render: tuple[Path, bytes, bytes], step_wav: Path, tmp_path: Path
) -> None:
    original_dir, png, json = first_render
    _, view = load_view(original_dir)
    duration = view["input"]["duration"]
    span = "0:10" if duration == 10.0 else f"0:{duration}"
    assert _render_bytes(step_wav, tmp_path / "explicit-span", span) == (png, json)


def test_d4_different_amplitude(first_render: tuple[Path, bytes, bytes], tmp_path: Path) -> None:
    _, png, json = first_render
    changed = make_wav(tmp_path / "different.wav", [(5.0, 1000, 0.1), (5.0, 1000, 0.25)])
    changed_png, changed_json = _render_bytes(changed, tmp_path / "changed-input")
    assert changed_png != png
    assert changed_json != json
