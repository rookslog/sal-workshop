import json
from collections.abc import Iterator
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from conftest import comment_paths, served
from test_comments_http import _body, _sha256
from test_serve import _first_take


@pytest.fixture
def guard_server(comment_round: Path) -> Iterator[tuple[int, Path]]:
    with served(comment_round) as (url, root):
        port = urlsplit(url).port
        assert port is not None
        yield port, root


@pytest.fixture
def valid_comment(comment_round: Path) -> dict:
    return _body(_sha256(comment_round))


def _request(
    port: int,
    method: str,
    path: str,
    host: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    with closing(HTTPConnection("127.0.0.1", port, timeout=5)) as connection:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        connection.putheader("Host", host)
        for name, value in (headers or {}).items():
            connection.putheader(name, value)
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        with connection.getresponse() as response:
            return (
                response.status,
                {name.lower(): value for name, value in response.getheaders()},
                response.read(),
            )


def _post(
    port: int,
    record: dict,
    *,
    host: str | None = None,
    content_type: str | None = "application/json",
    origin: str | None = None,
) -> int:
    headers = {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    if origin is not None:
        headers["Origin"] = origin
    status, _, _ = _request(
        port,
        "POST",
        "/comments",
        host if host is not None else f"127.0.0.1:{port}",
        headers=headers,
        body=json.dumps(record).encode("utf-8"),
    )
    return status


def test_f1_accepts_both_loopback_hosts(guard_server: tuple[int, Path]) -> None:
    port, _ = guard_server
    for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
        status, _, _ = _request(port, "GET", "/", host)
        assert status == 200, host


def test_f2_rejects_invalid_host_for_reads(
    guard_server: tuple[int, Path], comment_round: Path
) -> None:
    port, _ = guard_server
    media = "/" + _first_take(comment_round)["media"]
    requests = [
        ("GET", "/"),
        ("GET", "/page.json"),
        ("GET", "/comments"),
        ("HEAD", "/"),
        ("GET", media),
    ]
    for host in (f"example.test:{port}", f"127.0.0.1:{port + 1}"):
        for method, path in requests:
            status, _, body = _request(port, method, path, host)
            assert (status, body) == (403, b""), (host, method, path)


def test_f3_rejects_invalid_host_for_posts(
    guard_server: tuple[int, Path], valid_comment: dict
) -> None:
    port, root = guard_server
    assert _post(port, valid_comment, host=f"example.test:{port}") == 403
    assert comment_paths(root) == []


def test_f4_requires_json_content_type(guard_server: tuple[int, Path], valid_comment: dict) -> None:
    port, root = guard_server
    for content_type in ("text/plain", None, "application/x-www-form-urlencoded"):
        assert _post(port, valid_comment, content_type=content_type) == 415, content_type
        assert comment_paths(root) == []


def test_f5_accepts_json_charset_without_origin(
    guard_server: tuple[int, Path], valid_comment: dict
) -> None:
    port, _ = guard_server
    assert _post(port, valid_comment, content_type="application/json; charset=utf-8") == 201


def test_f6_rejects_other_origins(guard_server: tuple[int, Path], valid_comment: dict) -> None:
    port, root = guard_server
    for origin in ("http://example.test", f"http://127.0.0.1:{port + 1}", "null"):
        assert _post(port, valid_comment, origin=origin) == 403, origin
        assert comment_paths(root) == []


def test_f7_accepts_both_loopback_origins(
    guard_server: tuple[int, Path], valid_comment: dict
) -> None:
    port, _ = guard_server
    assert _post(port, valid_comment, origin=f"http://127.0.0.1:{port}") == 201
    other_comment = {**valid_comment, "text": "second question"}
    assert _post(port, other_comment, origin=f"http://localhost:{port}") == 201


def test_f8_never_sends_cors_header(guard_server: tuple[int, Path], comment_round: Path) -> None:
    port, _ = guard_server
    media = "/" + _first_take(comment_round)["media"]
    for path, request_headers, expected in [
        ("/", {}, 200),
        ("/comments", {}, 200),
        (media, {"Range": "bytes=0-9"}, 206),
        ("/nosuch.txt", {}, 404),
    ]:
        status, headers, _ = _request(
            port, "GET", path, f"127.0.0.1:{port}", headers=request_headers
        )
        assert status == expected, path
        assert "access-control-allow-origin" not in headers, path
