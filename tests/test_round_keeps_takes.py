import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from shutil import copyfile

from conftest import (
    ROUND_LABELS,
    assert_schema,
    comment_files,
    http,
    make_audio_take,
    make_round,
    read_json,
    run_round_build,
    run_round_new,
    served,
)
from media import sha256_of
from test_comments_http import _body, _post_record


def test_t3_deleted_source_still_builds_from_kept_take(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    source = tmp_path / "take.mp4"
    copyfile(take_low_mp4, source)
    original = source.read_bytes()
    sha256 = sha256_of(source)
    created = run_round_new(tmp_path, [str(source), str(take_high_mp4)])
    assert created.returncode == 0, created.stderr
    source.unlink()

    built = run_round_build(tmp_path)
    assert built.returncode == 0, built.stderr
    media = tmp_path / "rounds" / "r1" / "build" / "takes" / sha256 / "media.mp4"
    assert media.read_bytes() == original


def test_t5_failed_round_leaves_no_media(tmp_path: Path, take_low_mp4: Path) -> None:
    result = run_round_new(tmp_path, [str(take_low_mp4), str(tmp_path / "missing.mp4")])
    assert result.returncode != 0
    round_dir = tmp_path / "rounds" / "r1"
    assert not (round_dir / "round.json").exists()
    assert not any(path.is_file() or path.is_symlink() for path in (round_dir / "media").rglob("*"))


def test_t6_only_one_concurrent_round_new_succeeds(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    command = [
        sys.executable,
        "-m",
        "sal",
        "round",
        "new",
        "r1",
        "--take",
        str(take_low_mp4),
        "--take",
        str(take_high_mp4),
        "--control",
        "1",
        "--view",
        "audio_basic",
        "--question",
    ]
    questions = ("First question?", "Second question?")
    processes: list[subprocess.Popen[str]] = []
    with ExitStack() as children:
        try:
            for question in questions:
                process = children.enter_context(
                    subprocess.Popen(
                        [*command, question],
                        cwd=tmp_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                )
                processes.append(process)
            output = [process.communicate(timeout=60) for process in processes]
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()

    winners = [q for q, p in zip(questions, processes, strict=True) if p.returncode == 0]
    assert len(winners) == 1, output
    record = read_json(tmp_path / "rounds" / "r1" / "round.json")
    assert record["question"] == winners[0]
    assert_schema("round-1", record)


def test_t7_blind_build_links_stay_in_round_media(
    blind_round: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    sources = (take_low_mp4, take_high_mp4)
    names = {f"{sha256_of(source)}{source.suffix}" for source in sources}
    forbidden = (*ROUND_LABELS, *(source.name for source in sources))
    media = blind_round.resolve() / "media"
    linked_names = set()

    for link in (blind_round / "build").rglob("*"):
        if link.is_symlink():
            target = link.resolve(strict=True)
            assert target.is_file() and target.is_relative_to(media), link
            assert target.name in names, link
            linked_names.add(target.name)
            assert all(secret not in os.readlink(link) for secret in forbidden), link
    assert linked_names == names


def test_t8_keeps_original_audio_extensions(tmp_path: Path) -> None:
    wav = make_audio_take(tmp_path / "take.wav", 440, "pcm_s16le")
    m4a = make_audio_take(tmp_path / "take.m4a", 660, "aac")
    created = run_round_new(tmp_path, [str(wav), str(m4a)], view="audio_basic")
    assert created.returncode == 0, created.stderr
    media = tmp_path / "rounds" / "r1" / "media"
    for source in (wav, m4a):
        assert (media / f"{sha256_of(source)}{source.suffix}").is_file()

    built = run_round_build(tmp_path)
    assert built.returncode == 0, built.stderr


def test_t9_moved_round_rebuilds_serves_media_and_saves_comments(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    original_root = tmp_path / "original"
    original_root.mkdir()
    round_dir = make_round(original_root, (take_low_mp4, take_high_mp4))

    destination_root = tmp_path / "destination"
    moved_round = destination_root / "rounds" / "r1"
    moved_round.parent.mkdir(parents=True)
    round_dir.rename(moved_round)

    built = run_round_build(destination_root)
    assert built.returncode == 0, built.stderr
    take = read_json(moved_round / "build" / "page.json")["takes"][0]
    with served(moved_round) as (url, _):
        status, _, media = http("GET", url + take["media"])
        assert status == 200
        assert media == take_low_mp4.read_bytes()
        record = _post_record(url, _body(take["sha256"]))

    assert comment_files(destination_root) == [record]
