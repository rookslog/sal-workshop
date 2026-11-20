import socket
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from conftest import http

from sal.server import create_server


@pytest.fixture
def bound_server(
    comment_round: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ThreadingHTTPServer, list[str]]]:
    lookups: list[str] = []

    def unexpected_lookup(name: str = "") -> str:
        lookups.append(name)
        raise AssertionError(f"socket.getfqdn called with {name!r}")

    monkeypatch.setattr(socket, "getfqdn", unexpected_lookup)
    with create_server(comment_round, port=0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server, lookups
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_b1_serves_without_name_lookup(
    bound_server: tuple[ThreadingHTTPServer, list[str]],
) -> None:
    server, lookups = bound_server
    port = server.server_address[1]
    host = f"127.0.0.1:{port}"

    status, _, _ = http("GET", f"http://{host}/", headers={"Host": host})

    assert status == 200
    assert lookups == []


def test_b2_binds_to_loopback_and_ephemeral_port(
    bound_server: tuple[ThreadingHTTPServer, list[str]],
) -> None:
    server, _ = bound_server
    host, port = server.server_address

    assert host == "127.0.0.1"
    assert port > 0
