import re
import signal
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from conftest import http, read_json, run_round_new, run_sal, serve_process, served


def _first_take(round_dir: Path) -> dict:
    return read_json(round_dir / "build" / "page.json")["takes"][0]


def test_s1_loopback_address(comment_round: Path) -> None:
    from sal.server import create_server

    server = create_server(comment_round)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_s2_url_announcement(comment_round: Path) -> None:
    with served(comment_round) as (url, _):
        assert re.fullmatch(r"http://127\.0\.0\.1:\d+/", url)


@pytest.mark.parametrize(
    ("request_path", "file_name", "content_type"),
    [
        ("", "index.html", "text/html"),
        ("index.html", "index.html", "text/html"),
        ("page.js", "page.js", "text/javascript"),
        ("page.json", "page.json", "application/json"),
        ("view.png", "view.png", "image/png"),
    ],
    ids=["root", "index", "script", "manifest", "view"],
)
def test_s3_static_files(
    comment_round: Path, request_path: str, file_name: str, content_type: str
) -> None:
    if file_name == "view.png":
        request_path = file_name = _first_take(comment_round)["view"]["png"]
    expected = (comment_round / "build" / file_name).read_bytes()
    with served(comment_round) as (url, _):
        status, headers, body = http("GET", url + request_path)
    assert status == 200
    assert re.fullmatch(rf"{re.escape(content_type)}(?:;\s*charset=.+)?", headers["content-type"])
    assert headers["accept-ranges"] == "bytes"
    assert headers["content-length"] == str(len(expected))
    assert body == expected


def test_s4_media_link(comment_round: Path, take_low_mp4: Path) -> None:
    with served(comment_round) as (url, _):
        status, headers, body = http("GET", url + _first_take(comment_round)["media"])
    assert status == 200
    assert headers["content-type"] == "video/mp4"
    assert body == take_low_mp4.read_bytes()


@pytest.mark.parametrize("range_form", ["first", "middle", "open", "suffix"])
def test_s5_ranges(comment_round: Path, take_low_mp4: Path, range_form: str) -> None:
    expected = take_low_mp4.read_bytes()
    size = len(expected)
    ranges = {
        "first": ("0-99", 0, 99),
        "middle": ("100-199", 100, 199),
        "open": (f"{size - 50}-", size - 50, size - 1),
        "suffix": ("-50", size - 50, size - 1),
    }
    requested, start, end = ranges[range_form]
    with served(comment_round) as (url, _):
        status, headers, body = http(
            "GET",
            url + _first_take(comment_round)["media"],
            headers={"Range": f"bytes={requested}"},
        )
    assert status == 206
    assert headers["content-range"] == f"bytes {start}-{end}/{size}"
    assert body == expected[start : end + 1]


def test_s6_out_of_range(comment_round: Path, take_low_mp4: Path) -> None:
    size = take_low_mp4.stat().st_size
    with served(comment_round) as (url, _):
        status, headers, _ = http(
            "GET",
            url + _first_take(comment_round)["media"],
            headers={"Range": f"bytes={size + 10}-"},
        )
    assert status == 416
    assert headers["content-range"] == f"bytes */{size}"


def test_s7_head(comment_round: Path, take_low_mp4: Path) -> None:
    with served(comment_round) as (url, _):
        status, headers, body = http("HEAD", url + _first_take(comment_round)["media"])
    assert status == 200
    assert headers["content-length"] == str(take_low_mp4.stat().st_size)
    assert body == b""


@pytest.mark.parametrize(
    "path",
    [
        "/build.private.json",
        "/../build.private.json",
        "/..%2Fbuild.private.json",
        "/%2e%2e/round.json",
        "/takes/../../round.json",
        "/nosuch.txt",
    ],
)
def test_s8_private_paths_unreachable(comment_round: Path, path: str) -> None:
    with served(comment_round) as (url, _):
        with closing(HTTPConnection("127.0.0.1", urlsplit(url).port, timeout=5)) as connection:
            connection.request("GET", path)
            with connection.getresponse() as response:
                assert response.status == 404


def test_s10_unbuilt_round_refused(tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path) -> None:
    created = run_round_new(tmp_path, [str(take_low_mp4), str(take_high_mp4)])
    assert created.returncode == 0, created.stderr
    assert not (tmp_path / "rounds" / "r1" / "build").exists()

    result = run_sal("round", "serve", "rounds/r1", "--port", "0", cwd=tmp_path)
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip()


def test_s11_sigterm_exits_cleanly(comment_round: Path) -> None:
    with serve_process(comment_round) as (process, _):
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 0
