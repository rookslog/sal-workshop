import json
import os
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from shutil import copyfile

import pytest
from conftest import (
    ROUND_LABELS,
    assert_fixed_json,
    assert_schema,
    load_view,
    make_round,
    read_json,
    run_round_build,
    run_round_new,
)
from media import sha256_of


def _files_under(directory: Path) -> dict[str, Path]:
    return {
        path.relative_to(directory).as_posix(): path
        for path in directory.rglob("*")
        if path.is_file() or path.is_symlink()
    }


def _round_takes(round_dir: Path) -> list[dict]:
    return read_json(round_dir / "round.json")["takes"]


def _page_takes(round_dir: Path) -> list[dict]:
    return read_json(round_dir / "build" / "page.json")["takes"]


def _private_takes(round_dir: Path) -> dict[str, dict]:
    return {take["sha256"]: take for take in read_json(round_dir / "build.private.json")["takes"]}


class _PageDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[list[str]] = []
        self.in_page_data = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            attributes = dict(attrs)
            self.in_page_data = (
                attributes.get("id") == "page-data" and attributes.get("type") == "application/json"
            )
            if self.in_page_data:
                self.scripts.append([])

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.in_page_data = False

    def handle_data(self, data: str) -> None:
        if self.in_page_data:
            self.scripts[-1].append(data)


def _embedded_page_data(build: Path) -> dict:
    parser = _PageDataParser()
    parser.feed((build / "index.html").read_text(encoding="utf-8"))
    parser.close()
    assert len(parser.scripts) == 1
    return json.loads("".join(parser.scripts[0]))


def test_rb1_build_stdout_and_files(
    plain_round: Path, plain_round_build: tuple[Path, subprocess.CompletedProcess[str]]
) -> None:
    _, process = plain_round_build
    assert len(process.stdout.splitlines()) == 1
    output = json.loads(process.stdout)
    assert output.keys() == {"build", "seed"}
    assert output["build"] == "rounds/r1/build"
    assert type(output["seed"]) is int

    expected = {"index.html", "page.js", "page.json"}
    for take in _round_takes(plain_round):
        base = f"takes/{take['sha256']}"
        extension = Path(take["path"]).suffix
        expected.update(
            {
                f"{base}/media{extension}",
                f"{base}/audio_basic/view.png",
                f"{base}/audio_basic/view.json",
            }
        )
    assert _files_under(plain_round / "build").keys() == expected


def test_rb2_build_schemas_and_json(plain_round: Path) -> None:
    build = plain_round / "build"
    assert_schema("page-1", assert_fixed_json(build / "page.json"))
    assert_schema("build-private-1", read_json(plain_round / "build.private.json"))
    assert not list(build.rglob("build.private.json"))


def test_rb3_media_symlinks_and_views(plain_round: Path) -> None:
    for take in _round_takes(plain_round):
        directory = plain_round / "build" / "takes" / take["sha256"]
        link = directory / "media.mp4"
        assert link.is_symlink()
        assert os.path.samefile(link, take["path"])
        _, view = load_view(directory / "audio_basic")
        assert_schema("view-1", view)
        assert view["input"]["sha256"] == take["sha256"]


def test_rb4_page_names_and_view_metadata(plain_round: Path) -> None:
    build = plain_round / "build"
    takes = _page_takes(plain_round)
    assert [take["shown"] for take in takes] == list(ROUND_LABELS)
    for take, source in zip(takes, _round_takes(plain_round), strict=True):
        base = f"takes/{source['sha256']}"
        _, view = load_view(build / base / "audio_basic")
        assert take["media"] == f"{base}/media.mp4"
        assert (build / take["media"]).is_file()
        shown_view = take["view"]
        assert shown_view["png"] == f"{base}/audio_basic/view.png"
        assert (build / shown_view["png"]).is_file()
        for key in ("width", "height"):
            assert shown_view[key] == view["image"][key]
        assert shown_view["ruler"] == {key: view["ruler"][key] for key in ("x0", "x1", "t0", "t1")}


def test_rb5_loudness_and_matching_gains(
    plain_round: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    private = _private_takes(plain_round)
    low = private[sha256_of(take_low_mp4)]
    high = private[sha256_of(take_high_mp4)]
    assert low["lufs"] == pytest.approx(-20.0, abs=1.5)
    assert high["lufs"] == pytest.approx(-14.0, abs=1.5)
    assert low["gain_db"] == 0.0
    assert high["gain_db"] == pytest.approx(-6.0, abs=0.3)
    assert [take["gain_db"] for take in _page_takes(plain_round)] == [
        low["gain_db"],
        high["gain_db"],
    ]
    assert all(take["gain_db"] <= 0 for take in private.values())


def test_rb6_blind_letters_map_to_original_takes(blind_round: Path) -> None:
    takes = _page_takes(blind_round)
    assert [take["shown"] for take in takes] == ["A", "B"]
    private = _private_takes(blind_round)
    originals = {take["sha256"]: take for take in _round_takes(blind_round)}
    assert {take["sha256"] for take in takes} == private.keys() == originals.keys()
    for take in takes:
        record = private[take["sha256"]]
        original = originals[take["sha256"]]
        assert record["shown"] == take["shown"]
        assert record["label"] == original["label"]
        assert record["control"] is original["control"]


def _assert_no_private_keys(value: object) -> None:
    if isinstance(value, dict):
        assert not ({"label", "control", "path"} & value.keys())
        for child in value.values():
            _assert_no_private_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_private_keys(child)


def test_rb7_blind_build_leaks_nothing(
    blind_round: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    build = blind_round / "build"
    sources = (take_low_mp4, take_high_mp4)
    forbidden = (
        *ROUND_LABELS,
        *(path.name for path in sources),
        *(str(p.resolve()) for p in sources),
    )
    for path in _files_under(build).values():
        if not path.is_symlink() and path.is_file():
            data = path.read_bytes()
            for secret in forbidden:
                assert secret.encode("utf-8") not in data, (path, secret)
    _assert_no_private_keys(read_json(build / "page.json"))
    _assert_no_private_keys(_embedded_page_data(build))


def _shown_order(round_dir: Path) -> tuple[str, ...]:
    return tuple(take["sha256"] for take in _page_takes(round_dir))


def test_rb8_seed_varies_order_and_repeats(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    result = run_round_new(tmp_path, [str(take_low_mp4), str(take_high_mp4)], blind=True)
    assert result.returncode == 0, result.stderr
    round_dir = tmp_path / "rounds" / "r1"
    orders = []
    for seed in range(20):
        result = run_round_build(tmp_path, seed=seed)
        assert result.returncode == 0, result.stderr
        orders.append(_shown_order(round_dir))
    low_sha, high_sha = sha256_of(take_low_mp4), sha256_of(take_high_mp4)
    assert set(orders) == {(low_sha, high_sha), (high_sha, low_sha)}
    result = run_round_build(tmp_path, seed=7)
    assert result.returncode == 0, result.stderr
    assert _shown_order(round_dir) == orders[7]


def test_rb9_build_reuses_stored_seed(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    round_dir = make_round(tmp_path, (take_low_mp4, take_high_mp4), blind=True, seed=5)
    order = _shown_order(round_dir)
    result = run_round_build(tmp_path)
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == 1
    seed = json.loads(result.stdout)["seed"]
    assert type(seed) is int and seed == 5
    assert _shown_order(round_dir) == order


def test_rb10_same_seed_same_bytes_across_roots(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    roots = [tmp_path / name for name in ("first", "second")]
    for root in roots:
        root.mkdir()
    first, second = (root / "rounds" / "r1" for root in roots)
    process = run_round_new(roots[0], [str(take_low_mp4), str(take_high_mp4)])
    assert process.returncode == 0, process.stderr
    second.mkdir(parents=True)
    copyfile(first / "round.json", second / "round.json")
    for root in roots:
        process = run_round_build(root, seed=7)
        assert process.returncode == 0, process.stderr
    paths = ["page.json", "index.html", "page.js"]
    for source in (take_low_mp4, take_high_mp4):
        base = f"takes/{sha256_of(source)}/audio_basic"
        paths.extend([f"{base}/view.png", f"{base}/view.json"])
    for relative in paths:
        assert (first / "build" / relative).read_bytes() == (
            second / "build" / relative
        ).read_bytes()


def test_rb11_page_script_is_shared(plain_round: Path, blind_round: Path) -> None:
    script = "build/page.js"
    assert (plain_round / script).read_bytes() == (blind_round / script).read_bytes()


def test_rb12_index_embeds_page_json(plain_round: Path) -> None:
    build = plain_round / "build"
    assert _embedded_page_data(build) == read_json(build / "page.json")


def test_rb13_changed_take_refuses_first_build(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    source = tmp_path / "take.mp4"
    copyfile(take_low_mp4, source)
    result = run_round_new(tmp_path, [str(source), str(take_high_mp4)])
    assert result.returncode == 0, result.stderr
    copyfile(take_high_mp4, source)
    result = run_round_build(tmp_path)
    assert result.returncode != 0
    assert "sha256" in result.stderr
    assert not (tmp_path / "rounds" / "r1" / "build").exists()


def _snapshot_build(build: Path) -> dict[str, tuple[Path | None, bytes]]:
    return {
        name: (path.readlink() if path.is_symlink() else None, path.read_bytes())
        for name, path in _files_under(build).items()
    }


def test_rb14_changed_take_preserves_existing_build(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    source = tmp_path / "take.mp4"
    copyfile(take_low_mp4, source)
    build = make_round(tmp_path, (source, take_high_mp4)) / "build"
    copyfile(take_high_mp4, source)
    # The media symlink already reads the changed source before the refused build runs.
    before = _snapshot_build(build)
    assert before
    result = run_round_build(tmp_path)
    assert result.returncode != 0
    assert _snapshot_build(build) == before


def test_rb15_missing_round_refuses(tmp_path: Path) -> None:
    result = run_round_build(tmp_path, round_id="nosuch")
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip()
