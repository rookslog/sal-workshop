import re
import wave
from io import BytesIO
from pathlib import Path

import pytest
from conftest import git, make_repo, run_leak
from PIL import Image, PngImagePlugin

MEDIA_SUFFIXES = (
    "wav",
    "aif",
    "aiff",
    "flac",
    "mp3",
    "m4a",
    "aac",
    "ogg",
    "opus",
    "mp4",
    "mov",
    "mkv",
    "webm",
    "m4v",
    "avi",
)
HOME_PATH = "/".join(("", "home", "someone"))


def _commit_message(repo: Path, message: str) -> None:
    message_file = repo.parent / "commit-message.txt"
    message_file.write_text(f"{message}\n", encoding="utf-8")
    git(repo, "commit", "--allow-empty", "-F", str(message_file))


@pytest.mark.parametrize("suffix", [*MEDIA_SUFFIXES, *(name.upper() for name in MEDIA_SUFFIXES)])
def test_b13_media_extension(tmp_path: Path, suffix: str) -> None:
    filename = f"clip.{suffix}"
    repo = make_repo(tmp_path, {filename: "not really media"})
    result = run_leak(repo)

    assert result.returncode == 1
    assert result.stderr == f"{filename}:0: media-file\n"
    assert result.stdout == ""


@pytest.mark.parametrize(
    "marker_parts",
    [
        pytest.param((b"RIFF", bytes(4), b"WAVE"), id="riff-wave"),
        pytest.param((b"FORM", bytes(4), b"AIFF"), id="form-aiff"),
        pytest.param((b"FORM", bytes(4), b"AIFC"), id="form-aifc"),
        pytest.param((b"fL", b"aC"), id="flac"),
        pytest.param((b"I", b"D3"), id="id3"),
        pytest.param((b"Ogg", b"S"), id="ogg"),
        pytest.param((bytes(4), b"ftyp"), id="ftyp"),
        pytest.param((b"\x1a\x45", b"\xdf\xa3"), id="ebml"),
    ],
)
def test_b14_media_marker(tmp_path: Path, marker_parts: tuple[bytes, ...]) -> None:
    content = b"".join(marker_parts) + bytes(64)
    repo = make_repo(tmp_path, {"data.bin": content})
    result = run_leak(repo)

    assert result.returncode == 1
    assert result.stderr == "data.bin:0: media-file\n"
    assert result.stdout == ""


def test_b14b_wav_under_text_name(tmp_path: Path) -> None:
    fixture = tmp_path / "notes.txt"
    sample_rate = 8_000
    sample_width = 2
    with wave.open(str(fixture), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(sample_rate * sample_width))
    repo = make_repo(tmp_path / "repo", {fixture.name: fixture.read_bytes()})
    result = run_leak(repo)

    assert result.returncode == 1
    assert result.stderr == "notes.txt:0: media-file\n"
    assert result.stdout == ""


def test_b14c_png_and_json_allowed(tmp_path: Path) -> None:
    image = Image.new("RGB", (8, 8))
    original = BytesIO()
    image.save(original, format="PNG")
    # A private ancillary chunk pads the PNG without changing its image data.
    chunk_overhead = 12  # Length, type, and CRC fields.
    padding = PngImagePlugin.PngInfo()
    padding.add(b"paDd", bytes(2_000 - original.tell() - chunk_overhead))
    png = BytesIO()
    image.save(png, format="PNG", pnginfo=padding)
    assert len(png.getvalue()) == 2_000

    repo = make_repo(tmp_path, {"image.png": png.getvalue(), "notes.json": "{}\n"})
    result = run_leak(repo)

    assert result.returncode == 0
    assert result.stdout == "leak check: OK (2 files)\n"
    assert result.stderr == ""


def test_b15_large_file(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"big.bin": bytes(524_289)})
    result = run_leak(repo)

    assert result.returncode == 1
    assert result.stderr == "big.bin:0: large-file\n"
    assert result.stdout == ""


def test_b15b_size_limit_allowed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"ok.bin": bytes(524_288)})
    result = run_leak(repo)

    assert result.returncode == 0
    assert result.stdout == "leak check: OK (1 files)\n"
    assert result.stderr == ""


def test_b16_commit_message_home_path(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", {"notes.txt": "clean\n"})
    _commit_message(repo, f"Update notes\nMore details\n{HOME_PATH}")
    result = run_leak(repo)

    assert result.returncode == 1
    assert HOME_PATH not in result.stderr
    assert re.fullmatch(r"commit [0-9a-f]{7,40}:3: home-path\n", result.stderr)
    assert result.stdout == ""


def test_b16b_commit_message_email(tmp_path: Path) -> None:
    email = "@".join(("other", "example.org"))
    repo = make_repo(tmp_path / "repo", {"notes.txt": "clean\n"})
    _commit_message(repo, f"{email}\nMore details\nEnd of message")
    result = run_leak(repo)

    assert result.returncode == 1
    assert re.fullmatch(r"commit [0-9a-f]{7,40}:1: email\n", result.stderr)
    assert result.stdout == ""


def test_b16c_commit_message_denylist(tmp_path: Path) -> None:
    entry = " ".join(("Night", "Bus", "Seven"))
    denylist = tmp_path / "denylist.txt"
    denylist.write_text(f"{entry}\n", encoding="utf-8")
    repo = make_repo(tmp_path / "repo", {"notes.txt": "clean\n"})
    _commit_message(repo, entry.lower())
    blocked = run_leak(repo, denylist=denylist)
    allowed = run_leak(repo)

    assert blocked.returncode == 1
    assert re.fullmatch(r"commit [0-9a-f]{7,40}:1: denylist\n", blocked.stderr)
    assert blocked.stdout == ""
    assert allowed.returncode == 0
    assert allowed.stdout == "leak check: OK (1 files)\n"
    assert allowed.stderr == ""


def test_b16d_clean_commit_messages(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", {"notes.txt": "clean\n"})
    _commit_message(repo, "Fix the ruler")
    _commit_message(repo, "Add tests")
    result = run_leak(repo)

    assert result.returncode == 0
    assert result.stdout == "leak check: OK (1 files)\n"
    assert result.stderr == ""


def test_b16e_commit_message_media_link(tmp_path: Path) -> None:
    host = ".".join(("bandcamp", "com"))
    repo = make_repo(tmp_path / "repo", {"notes.txt": "clean\n"})
    _commit_message(repo, f"Update notes\nhttps://{host}/x")
    result = run_leak(repo)

    assert result.returncode == 1
    assert re.fullmatch(r"commit [0-9a-f]{7,40}:2: media-link\n", result.stderr)
    assert result.stdout == ""


def test_b18_sorted_file_and_commit_findings(tmp_path: Path) -> None:
    media_filename = ".".join(("a", "mp3"))
    repo = make_repo(
        tmp_path / "repo", {media_filename: "not really media", "b.bin": bytes(524_289)}
    )
    _commit_message(repo, HOME_PATH)
    result = run_leak(repo)

    assert result.returncode == 1
    assert re.fullmatch(
        rf"{re.escape(media_filename)}:0: media-file\nb\.bin:0: large-file\n"
        r"commit [0-9a-f]{7,40}:1: home-path\n",
        result.stderr,
    )
    assert result.stdout == ""
