import json
import os
import re
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from select import select
from threading import Thread
from time import monotonic
from typing import BinaryIO
from urllib.parse import urlsplit

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


@contextmanager
def _start_default_server(round_dir: Path) -> Iterator[subprocess.Popen[bytes]]:
    root = round_dir.parent.parent
    args = [sys.executable, "-m", "sal", "round", "serve", str(round_dir.relative_to(root))]
    with subprocess.Popen(args, cwd=root, stdout=subprocess.PIPE) as process:
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def _announced_url(stream: BinaryIO) -> str:
    deadline = monotonic() + 5
    pending = bytearray()
    while b"\n" not in pending:
        remaining = deadline - monotonic()
        assert remaining > 0 and select([stream], [], [], remaining)[0], (
            "round serve did not flush its URL"
        )
        chunk = os.read(stream.fileno(), 4096)
        assert chunk, "round serve closed stdout before announcing its URL"
        pending.extend(chunk)
    return json.loads(pending.partition(b"\n")[0])["url"]


def test_b3_default_port_is_free_for_each_server(comment_round: Path) -> None:
    with (
        _start_default_server(comment_round) as first,
        _start_default_server(comment_round) as second,
    ):
        assert first.stdout is not None and second.stdout is not None
        first_url = _announced_url(first.stdout)
        second_url = _announced_url(second.stdout)
        assert urlsplit(first_url).port != urlsplit(second_url).port

        for url in (first_url, second_url):
            status, _, _ = http("GET", url)
            assert status == 200, url


def test_b4_explicit_port_in_use_is_refused(comment_round: Path) -> None:
    root = comment_round.parent.parent
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "sal",
                "round",
                "serve",
                str(comment_round.relative_to(root)),
                "--port",
                str(port),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    assert result.returncode != 0
    assert result.stdout == ""
    assert len(result.stderr.splitlines()) == 1, result.stderr
    assert re.search(rf"\b{port}\b", result.stderr), result.stderr
    assert "in use" in result.stderr
    assert "--port" in result.stderr
    assert "Traceback" not in result.stderr
