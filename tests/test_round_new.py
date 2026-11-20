import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import ROUND_LABELS, assert_fixed_json, assert_schema, read_json, run_round_new
from media import make_wav, sha256_of


def _new_demo(root: Path, low: Path, high: Path) -> subprocess.CompletedProcess[str]:
    takes = [f"{path}::{label}" for path, label in zip((low, high), ROUND_LABELS, strict=True)]
    return run_round_new(root, takes, control=2, project="demo")


@pytest.fixture(scope="module")
def demo_round(
    tmp_path_factory: pytest.TempPathFactory, take_low_mp4: Path, take_high_mp4: Path
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    root = tmp_path_factory.mktemp("new-demo")
    process = _new_demo(root, take_low_mp4, take_high_mp4)
    assert process.returncode == 0, process.stderr
    return root / "rounds" / "r1" / "round.json", process


def test_rn1_new_round_metadata_and_format(
    demo_round: tuple[Path, subprocess.CompletedProcess[str]],
) -> None:
    path, process = demo_round
    assert len(process.stdout.splitlines()) == 1
    assert json.loads(process.stdout) == {"round": "rounds/r1/round.json"}
    data = assert_fixed_json(path)
    assert_schema("round-1", data)
    assert data["id"] == "r1"
    assert data["question"] == "Which one?"
    assert data["project"] == "demo"
    assert data["views"] == ["audio_basic"]
    assert data["blind"] is False


def test_rn2_take_metadata(
    demo_round: tuple[Path, subprocess.CompletedProcess[str]],
    take_low_mp4: Path,
    take_high_mp4: Path,
) -> None:
    takes = read_json(demo_round[0])["takes"]
    for take, source, label in zip(takes, (take_low_mp4, take_high_mp4), ROUND_LABELS, strict=True):
        assert take["sha256"] == sha256_of(source)
        assert take["path"] == f"media/{take['sha256']}.mp4"
        path = demo_round[0].parent / take["path"]
        assert path.is_file() and not path.is_symlink()
        assert path.read_bytes() == source.read_bytes()
        assert os.stat(path).st_mode & 0o222 == 0
        assert take["source"] == str(source.resolve())
        duration = take["duration"]
        assert abs(duration - 12.0) <= 0.06
        assert duration == round(duration, 3)
        assert take["label"] == label
    assert [take["control"] for take in takes] == [False, True]


def test_rn3_created_utc(demo_round: tuple[Path, subprocess.CompletedProcess[str]]) -> None:
    created = read_json(demo_round[0])["created"]
    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", created)
    timestamp = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert abs((datetime.now(UTC) - timestamp).total_seconds()) <= 120


def test_rn4_default_labels_and_project(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    process = run_round_new(tmp_path, [str(take_low_mp4), str(take_high_mp4)], blind=True)
    assert process.returncode == 0, process.stderr
    data = read_json(tmp_path / "rounds" / "r1" / "round.json")
    assert [take["label"] for take in data["takes"]] == ["take 1", "take 2"]
    assert data["project"] is None
    assert data["blind"] is True


def test_rn5_existing_round_is_not_overwritten(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    first = _new_demo(tmp_path, take_low_mp4, take_high_mp4)
    assert first.returncode == 0, first.stderr
    path = tmp_path / "rounds" / "r1" / "round.json"
    original = path.read_bytes()
    second = _new_demo(tmp_path, take_low_mp4, take_high_mp4)
    assert second.returncode != 0
    assert second.stdout == ""
    assert second.stderr.strip()
    assert path.read_bytes() == original


def test_rn6_existing_directory_is_preserved(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    path = tmp_path / "rounds" / "r1" / "takes.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"keep")
    process = _new_demo(tmp_path, take_low_mp4, take_high_mp4)
    assert process.returncode == 0, process.stderr
    assert path.read_bytes() == b"keep"


def _distinct_wavs(root: Path, count: int) -> list[str]:
    return [
        str(make_wav(root / f"take-{index}.wav", [(1.0, 1000, index / 20)]))
        for index in range(1, count + 1)
    ]


@pytest.mark.parametrize(
    "case",
    [
        "one-take",
        "ten-takes",
        "duplicate",
        "control-three",
        "control-zero",
        "unknown-view",
        "missing",
        "not-media",
        "bad-id",
        "dash-id",
    ],
)
def test_rn7_invalid_round_refuses(
    case: str, tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    takes = [str(take_low_mp4), str(take_high_mp4)]
    round_id, control, view = "r1", 1, "audio_basic"

    match case:
        case "one-take":
            takes.pop()
        case "ten-takes":
            takes = _distinct_wavs(tmp_path, 10)
        case "duplicate":
            takes[1] = takes[0]
        case "control-three":
            control = 3
        case "control-zero":
            control = 0
        case "unknown-view":
            view = "nosuchview"
        case "missing":
            takes[1] = str(tmp_path / "missing.mp4")
        case "not-media":
            text_file = tmp_path / "not-media.txt"
            text_file.write_text("not media", encoding="utf-8")
            takes[1] = str(text_file)
        case "bad-id":
            round_id = "Bad_ID"
        case "dash-id":
            round_id = "-x"
        case _:
            pytest.fail(f"Unknown refusal case: {case}")

    process = run_round_new(tmp_path, takes, round_id=round_id, control=control, view=view)
    assert process.returncode != 0
    assert process.stdout == ""
    assert process.stderr.strip()
    assert not (tmp_path / "rounds" / round_id / "round.json").exists()


def test_rn8_nine_takes(tmp_path: Path) -> None:
    process = run_round_new(tmp_path, _distinct_wavs(tmp_path, 9))
    assert process.returncode == 0, process.stderr
    assert len(read_json(tmp_path / "rounds" / "r1" / "round.json")["takes"]) == 9
