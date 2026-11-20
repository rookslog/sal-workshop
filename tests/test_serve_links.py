from pathlib import Path

import pytest
from conftest import comment_files, http, make_round, run_round_build, run_sal, served
from test_comments_http import _body, _post_record
from test_round_build import _private_takes, _round_takes
from test_serve import _first_take


def test_s1_only_serves_links_into_this_rounds_media(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    round_dir = make_round(tmp_path, (take_low_mp4, take_high_mp4))
    build = round_dir / "build"
    media = Path(_first_take(round_dir)["media"])
    outside = tmp_path / "outside.txt"
    outside.write_text("not public", encoding="utf-8")
    (build / "extra.txt").symlink_to(outside)
    (build / media.parent / "other.mp4").symlink_to(take_high_mp4)

    with served(round_dir) as (url, _):
        assert http("GET", url + "extra.txt")[0] == 404
        assert http("GET", url + (media.parent / "other.mp4").as_posix())[0] == 404
        status, _, body = http("GET", url + media.as_posix())
        assert status == 200
        assert body == take_low_mp4.read_bytes()


def test_s2_refuses_symlinked_build(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    round_dir = make_round(tmp_path, (take_low_mp4, take_high_mp4))
    build = round_dir / "build"
    moved = build.rename(round_dir / "saved-build")
    build.symlink_to(moved, target_is_directory=True)

    result = run_sal("round", "serve", "rounds/r1", "--port", "0", cwd=tmp_path)
    assert result.returncode != 0
    assert "http://" not in result.stdout


def test_s3_only_blind_round_requires_private_build(
    blind_round: Path, tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    (blind_round / "build.private.json").unlink()
    result = run_sal(
        "round", "serve", f"rounds/{blind_round.name}", "--port", "0", cwd=blind_round.parent.parent
    )
    assert result.returncode != 0
    assert "build.private.json" in result.stderr

    plain = make_round(tmp_path, (take_low_mp4, take_high_mp4))
    (plain / "build.private.json").unlink()
    take = _round_takes(plain)[0]
    with served(plain) as (url, root):
        saved = _post_record(url, _body(take["sha256"]))
        assert saved["take"]["label_shown"] == take["label"]
        assert comment_files(root) == [saved]


def test_s4_comment_uses_letter_of_rebuilt_round(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    round_dir = make_round(tmp_path, (take_low_mp4, take_high_mp4), blind=True, seed=0)
    sha256 = _round_takes(round_dir)[0]["sha256"]
    initial_letter = _private_takes(round_dir)[sha256]["shown"]

    with served(round_dir) as (url, _):
        for seed in range(1, 20):
            result = run_round_build(tmp_path, seed=seed)
            assert result.returncode == 0, result.stderr
            letter = _private_takes(round_dir)[sha256]["shown"]
            if letter != initial_letter:
                break
        else:
            pytest.fail("No seed in the WP-30 seed range reverses the blind order")

        record = _post_record(url, _body(sha256))
        assert record["take"] == {"sha256": sha256, "label_shown": letter}
