"""Local HTTP server for reviewing one round and saving comments."""

from __future__ import annotations

import json
import mimetypes
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from sal.comments import (
    OPERATOR_KINDS,
    build_comment_record,
    format_json_bytes,
    load_comments,
    load_round_context,
    validate_span_bounds,
    write_comment,
)

MAX_POST_BODY_BYTES = 65_536
POST_COMMENT_KEYS = frozenset({"sha256", "start", "end", "kind", "text"})
CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".opus": "audio/ogg",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
}
STREAM_CHUNK_SIZE = 65_536


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant: {value}")


def _content_type_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in CONTENT_TYPES:
        return CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _parse_byte_range(header_value: str | None, size: int) -> tuple[str, int, int]:
    """Parse HTTP ``Range`` header into ``('full'|'partial'|'invalid', start, end)``."""
    if header_value is None:
        return ("full", 0, max(0, size - 1))

    raw = header_value.strip()
    if not raw.lower().startswith("bytes="):
        return ("full", 0, max(0, size - 1))

    spec = raw[6:].strip()
    if not spec or "," in spec or "-" not in spec:
        return ("invalid", 0, 0)

    start_str, _, end_str = spec.partition("-")
    start_str = start_str.strip()
    end_str = end_str.strip()
    if not start_str and not end_str:
        return ("invalid", 0, 0)
    if size <= 0:
        return ("invalid", 0, 0)

    try:
        if not start_str:
            suffix = int(end_str)
            if suffix <= 0:
                return ("invalid", 0, 0)
            start = max(0, size - suffix)
            end = size - 1
            return ("partial", start, end)

        start = int(start_str)
        if start < 0 or start >= size:
            return ("invalid", 0, 0)

        if not end_str:
            end = size - 1
        else:
            end = int(end_str)
            if end < start:
                return ("invalid", 0, 0)
            end = min(end, size - 1)
        return ("partial", start, end)
    except ValueError:
        return ("invalid", 0, 0)


def _resolve_build_file(
    build_dir: Path, build_resolved: Path, media_dir: Path, request_target: str
) -> Path | None:
    """Resolve ``request_target`` inside ``build_dir``, following links only into ``media/``."""
    raw_path = urlsplit(request_target).path
    if "\x00" in raw_path or "\\" in raw_path:
        return None
    decoded = unquote(raw_path)
    if "\x00" in decoded or "\\" in decoded:
        return None

    if decoded == "/":
        parts = ["index.html"]
    else:
        if not decoded.startswith("/"):
            return None
        parts = decoded[1:].split("/")
        if any(part in ("", ".", "..") for part in parts):
            return None

    # Ensure intermediate directories never traverse or symlink outside build/.
    current = build_dir
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            return None

    if not current.resolve().is_relative_to(build_resolved):
        return None

    candidate = current / parts[-1]
    if candidate.is_symlink():
        if media_dir.is_symlink() or not media_dir.is_dir():
            return None
        media_resolved = media_dir.resolve()
        try:
            target = candidate.resolve(strict=True)
        except OSError:
            return None
        if not target.is_file() or not target.is_relative_to(media_resolved):
            return None
        return target

    if not candidate.is_file():
        return None
    if not candidate.resolve().is_relative_to(build_resolved):
        return None
    return candidate


class RoundHTTPServer(ThreadingHTTPServer):
    """Loopback-only HTTP server serving one built round and comment endpoints."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(
        self,
        server_address: tuple[str, int],
        round_id: str,
        root: Path,
        round_dir: Path,
        build_dir: Path,
    ) -> None:
        self.round_id = round_id
        self.root = root
        self.round_dir = round_dir
        self.media_dir = round_dir / "media"
        self.build_dir = build_dir
        self.build_resolved = build_dir.resolve()
        super().__init__(server_address, _RoundRequestHandler)


class _RoundRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for round static assets, byte-range media, and comments."""

    server: RoundHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        """Silence standard access logs on stderr."""
        del format, args

    def _send_empty(self, status: int, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()

    def _send_json(self, status: int, payload: Any) -> None:
        body = format_json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._write_bytes(body)

    def _write_bytes(self, data: bytes) -> None:
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_file(self, path: Path, *, include_body: bool) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            self._send_empty(404)
            return

        range_kind, start, end = _parse_byte_range(self.headers.get("Range"), size)
        if range_kind == "invalid":
            self._send_empty(
                416,
                {
                    "Accept-Ranges": "bytes",
                    "Content-Range": f"bytes */{size}",
                },
            )
            return

        content_type = _content_type_for(path)
        if range_kind == "partial":
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()
        else:
            length = size
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.end_headers()

        if not include_body or length == 0:
            return

        try:
            with path.open("rb") as stream:
                if start > 0:
                    stream.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = stream.read(min(STREAM_CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _has_valid_host(self) -> bool:
        hosts = self.headers.get_all("Host")
        if not hosts or len(hosts) != 1:
            return False
        port = self.server.server_address[1]
        return hosts[0].strip().lower() in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _has_valid_origin(self) -> bool:
        origins = self.headers.get_all("Origin")
        if not origins:
            return True
        if len(origins) != 1:
            return False
        port = self.server.server_address[1]
        return origins[0].strip().lower() in {
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        }

    def _has_json_content_type(self) -> bool:
        content_types = self.headers.get_all("Content-Type")
        if not content_types or len(content_types) != 1:
            return False
        media_type = content_types[0].split(";", 1)[0].strip().lower()
        return media_type == "application/json"

    def _handle_get_or_head(self, *, include_body: bool) -> None:
        if not self._has_valid_host():
            self._send_empty(403)
            return
        route_path = urlsplit(self.path).path
        if route_path == "/comments":
            records = [
                record
                for record in load_comments(self.server.root)
                if record["round"] == self.server.round_id
            ]
            body = format_json_bytes(records)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if include_body:
                self._write_bytes(body)
            return

        file_path = _resolve_build_file(
            self.server.build_dir, self.server.build_resolved, self.server.media_dir, self.path
        )
        if file_path is None:
            self._send_empty(404)
            return
        self._serve_file(file_path, include_body=include_body)

    def do_GET(self) -> None:
        self._handle_get_or_head(include_body=True)

    def do_HEAD(self) -> None:
        self._handle_get_or_head(include_body=False)

    def do_POST(self) -> None:
        if not self._has_valid_host():
            self._send_empty(403)
            return
        route_path = urlsplit(self.path).path
        if route_path != "/comments":
            self._send_empty(404)
            return
        if not self._has_valid_origin():
            self._send_empty(403)
            return
        if not self._has_json_content_type():
            self._send_empty(415)
            return

        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._send_empty(400)
            return
        try:
            content_length = int(raw_length)
        except ValueError:
            self._send_empty(400)
            return
        if content_length < 0 or content_length > MAX_POST_BODY_BYTES:
            self._send_empty(400)
            return

        raw_body = self.rfile.read(content_length)
        if len(raw_body) != content_length:
            self._send_empty(400)
            return

        try:
            payload = json.loads(raw_body.decode("utf-8"), parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self._send_empty(400)
            return

        if not isinstance(payload, dict) or set(payload.keys()) != POST_COMMENT_KEYS:
            self._send_empty(400)
            return

        sha256 = payload["sha256"]
        if not isinstance(sha256, str):
            self._send_empty(400)
            return

        kind = payload["kind"]
        if not isinstance(kind, str) or kind not in OPERATOR_KINDS:
            self._send_empty(400)
            return

        text = payload["text"]
        if not isinstance(text, str):
            self._send_empty(400)
            return

        try:
            round_id, duration_by_sha, label_shown_by_sha = load_round_context(
                self.server.round_dir
            )
        except (ValueError, OSError):
            self._send_empty(500)
            return

        if sha256 not in duration_by_sha:
            self._send_empty(400)
            return

        try:
            start, end = validate_span_bounds(
                payload["start"], payload["end"], duration_by_sha[sha256]
            )
            record = build_comment_record(
                round_id=round_id,
                sha256=sha256,
                label_shown=label_shown_by_sha[sha256],
                start=start,
                end=end,
                kind=kind,
                text=text,
                author="operator",
                channel="page",
            )
        except ValueError:
            self._send_empty(400)
            return

        try:
            write_comment(self.server.root, record)
        except FileExistsError:
            self._send_empty(409)
            return
        except ValueError:
            self._send_empty(400)
            return
        except OSError:
            self._send_empty(500)
            return

        self._send_json(201, record)


def create_server(round_dir: Path, port: int = 0) -> ThreadingHTTPServer:
    """Create a loopback-only ``ThreadingHTTPServer`` for a built round."""
    if port < 0 or port > 65_535:
        raise ValueError(f"port must be between 0 and 65535, got {port}")

    resolved_round = round_dir.resolve()
    build_dir = resolved_round / "build"
    if build_dir.is_symlink():
        raise ValueError(f"refusing to serve symlinked build directory: {build_dir}")
    if not build_dir.is_dir():
        raise ValueError(f"round has not been built (missing {build_dir})")

    round_id, _, _ = load_round_context(resolved_round)
    root = resolved_round.parent.parent
    return RoundHTTPServer(
        ("127.0.0.1", port),
        round_id=round_id,
        root=root,
        round_dir=resolved_round,
        build_dir=build_dir,
    )


def serve_round(round_dir: Path, *, port: int = 8765) -> int:
    """Serve ``round_dir`` on 127.0.0.1, print its URL line, and stop cleanly on signals."""
    server = create_server(round_dir, port=port)
    actual_port = server.server_address[1]
    stop_event = threading.Event()

    def _on_signal(_signum: int, _frame: Any) -> None:
        stop_event.set()

    prev_int = signal.signal(signal.SIGINT, _on_signal)
    prev_term = signal.signal(signal.SIGTERM, _on_signal)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print(json.dumps({"url": f"http://127.0.0.1:{actual_port}/"}), flush=True)
        stop_event.wait()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)
    return 0
