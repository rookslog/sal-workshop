import subprocess
from pathlib import Path

import pytest
from conftest import render


@pytest.fixture(params=[False, True], ids=["missing-out", "existing-out"])
def refusal_output(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[Path, bool]:
    out_dir = tmp_path / "out"
    existing = request.param
    if existing:
        out_dir.mkdir()
        (out_dir / "view.json").write_bytes(b"sentinel")
    return out_dir, existing


def _assert_refused(result: subprocess.CompletedProcess[str], output: tuple[Path, bool]) -> None:
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr != ""
    out_dir, existing = output
    if existing:
        sentinel = out_dir / "view.json"
        assert set(out_dir.iterdir()) == {sentinel}
        assert sentinel.read_bytes() == b"sentinel"
    else:
        assert not out_dir.exists()
        assert not out_dir.is_symlink()


def test_e1_missing_input(tmp_path: Path, refusal_output: tuple[Path, bool]) -> None:
    result = render("audio_basic", tmp_path / "missing.wav", refusal_output[0])
    _assert_refused(result, refusal_output)


def test_e2_text_input(tmp_path: Path, refusal_output: tuple[Path, bool]) -> None:
    input_path = tmp_path / "invalid.txt"
    input_path.write_text("not media", encoding="utf-8")
    result = render("audio_basic", input_path, refusal_output[0])
    _assert_refused(result, refusal_output)


def test_e3_unknown_view(step_wav: Path, refusal_output: tuple[Path, bool]) -> None:
    result = render("nosuchview", step_wav, refusal_output[0])
    _assert_refused(result, refusal_output)
    assert "audio_basic" in result.stderr


def test_e4_reversed_span(step_wav: Path, refusal_output: tuple[Path, bool]) -> None:
    result = render("audio_basic", step_wav, refusal_output[0], "6:2")
    _assert_refused(result, refusal_output)


def test_e5_empty_span(step_wav: Path, refusal_output: tuple[Path, bool]) -> None:
    result = render("audio_basic", step_wav, refusal_output[0], "2:2")
    _assert_refused(result, refusal_output)


def test_e6_span_past_end(step_wav: Path, refusal_output: tuple[Path, bool]) -> None:
    result = render("audio_basic", step_wav, refusal_output[0], "2:99")
    _assert_refused(result, refusal_output)


def test_e7_invalid_span(step_wav: Path, refusal_output: tuple[Path, bool]) -> None:
    result = render("audio_basic", step_wav, refusal_output[0], "abc")
    _assert_refused(result, refusal_output)


def test_e8_negative_span_start(step_wav: Path, refusal_output: tuple[Path, bool]) -> None:
    result = render("audio_basic", step_wav, refusal_output[0], "-1:3")
    _assert_refused(result, refusal_output)


def test_e9_no_audio_stream(tmp_path: Path, refusal_output: tuple[Path, bool]) -> None:
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
    result = render("audio_basic", video, refusal_output[0])
    _assert_refused(result, refusal_output)
    assert "audio" in result.stderr.lower()


def test_e10_nested_output_directory(
    step_wav: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out_dir = Path("a") / "b" / "c"
    assert not Path("a").exists()
    result = render("audio_basic", step_wav, out_dir)
    assert result.returncode == 0, result.stderr
    assert (out_dir / "view.png").is_file()
    assert (out_dir / "view.json").is_file()


def test_e11_replace_existing_view(tmp_path: Path, step_wav: Path, silent_wav: Path) -> None:
    existing = tmp_path / "existing"
    fresh = tmp_path / "fresh"
    first = render("audio_basic", silent_wav, existing)
    assert first.returncode == 0, first.stderr
    assert (existing / "view.png").is_file()
    assert (existing / "view.json").is_file()
    replacement = render("audio_basic", step_wav, existing)
    assert replacement.returncode == 0, replacement.stderr
    baseline = render("audio_basic", step_wav, fresh)
    assert baseline.returncode == 0, baseline.stderr
    for name in ("view.png", "view.json"):
        assert (existing / name).read_bytes() == (fresh / name).read_bytes()
