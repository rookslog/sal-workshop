"""Comment record creation, validation, atomic storage, and loading."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeGuard

COMMENT_ID_RE = re.compile(r"^c-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}\Z")
CREATED_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
ROUND_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\Z")
SHA256_RE = re.compile(r"^[0-9a-f]{64}\Z")
AUTHOR_RE = re.compile(r"^(operator|agent:[a-z0-9-]+/[a-z0-9.-]+)\Z")
AGENT_AUTHOR_RE = re.compile(r"^agent:[a-z0-9-]+/[a-z0-9.-]+\Z")

VALID_KINDS = frozenset({"flag", "question", "request", "verdict", "answer"})
OPERATOR_KINDS = frozenset({"flag", "question", "request", "verdict"})
VALID_CHANNELS = frozenset({"page", "chat-transcribed", "agent"})
COMMENT_KEYS = frozenset(
    {
        "schema",
        "id",
        "created",
        "round",
        "take",
        "span",
        "kind",
        "text",
        "author",
        "channel",
        "answers",
        "replaces",
    }
)
TAKE_KEYS = frozenset({"sha256", "label_shown"})
SPAN_KEYS = frozenset({"start", "end"})
SPAN_TOLERANCE = 0.05


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant: {value}")


def format_json_bytes(payload: Mapping[str, Any] | list[Any]) -> bytes:
    """Serialize ``payload`` in canonical slice-1 UTF-8 JSON with trailing newline."""
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def _clean_zero(value: float) -> float:
    return 0.0 if value == 0.0 else value


def _is_finite_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _has_non_space(text: str) -> bool:
    return any(not ch.isspace() for ch in text)


def comment_id(record: Mapping[str, Any]) -> str:
    """Compute the content-addressed ``c-<timestamp>-<hash6>`` identifier for a comment."""
    if not isinstance(record, Mapping):
        raise ValueError("comment record must be a mapping")
    created = record.get("created")
    if not isinstance(created, str) or not CREATED_RE.fullmatch(created):
        raise ValueError(f"invalid created timestamp: {created!r}")

    for_hash = dict(record)
    for_hash["id"] = ""
    digest = hashlib.sha256(format_json_bytes(for_hash)).hexdigest()[:6]
    stamp = created.replace("-", "").replace(":", "")
    return f"c-{stamp}-{digest}"


def _validate_comment_id_ref(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not COMMENT_ID_RE.fullmatch(value):
        raise ValueError(f"invalid {field} reference: {value!r}")


def validate_comment_record(record: Any, *, check_id: bool = True) -> None:
    """Validate a comment record against ``sal.comment/1`` and semantic rules."""
    if not isinstance(record, dict):
        raise ValueError("comment record must be an object")
    if set(record.keys()) != COMMENT_KEYS:
        raise ValueError("comment record has missing or extra keys")
    if record["schema"] != "sal.comment/1":
        raise ValueError(f"invalid comment schema: {record['schema']!r}")

    created = record["created"]
    if not isinstance(created, str) or not CREATED_RE.fullmatch(created):
        raise ValueError(f"invalid created timestamp: {created!r}")

    round_id = record["round"]
    if not isinstance(round_id, str) or not ROUND_ID_RE.fullmatch(round_id):
        raise ValueError(f"invalid round id: {round_id!r}")

    take = record["take"]
    if not isinstance(take, dict) or set(take.keys()) != TAKE_KEYS:
        raise ValueError("invalid take object in comment record")
    if not isinstance(take["sha256"], str) or not SHA256_RE.fullmatch(take["sha256"]):
        raise ValueError(f"invalid take sha256: {take['sha256']!r}")
    if not isinstance(take["label_shown"], str) or not take["label_shown"]:
        raise ValueError("take label_shown must be a non-empty string")

    span = record["span"]
    if not isinstance(span, dict) or set(span.keys()) != SPAN_KEYS:
        raise ValueError("invalid span object in comment record")
    start, end = span["start"], span["end"]
    if not (_is_finite_number(start) and _is_finite_number(end)):
        raise ValueError("span bounds must be finite numbers")
    if start < 0 or start > end:
        raise ValueError(f"invalid comment span: {start}..{end}")

    kind = record["kind"]
    if not isinstance(kind, str) or kind not in VALID_KINDS:
        raise ValueError(f"invalid comment kind: {kind!r}")

    text = record["text"]
    if not isinstance(text, str):
        raise ValueError("comment text must be a string")
    if kind in ("question", "request") and not _has_non_space(text):
        raise ValueError(f"{kind} comment text must contain a non-space character")

    author = record["author"]
    if not isinstance(author, str) or not AUTHOR_RE.fullmatch(author):
        raise ValueError(f"invalid comment author: {author!r}")

    channel = record["channel"]
    if not isinstance(channel, str) or channel not in VALID_CHANNELS:
        raise ValueError(f"invalid comment channel: {channel!r}")

    _validate_comment_id_ref(record["answers"], "answers")
    _validate_comment_id_ref(record["replaces"], "replaces")
    if kind == "answer" and record["answers"] is None:
        raise ValueError("answer comment must reference an existing comment in 'answers'")

    cid = record["id"]
    if not isinstance(cid, str) or not COMMENT_ID_RE.fullmatch(cid):
        raise ValueError(f"invalid comment id: {cid!r}")
    if check_id:
        expected = comment_id(record)
        if cid != expected:
            raise ValueError(f"comment id {cid!r} does not match content hash {expected!r}")

    # Ensure canonical UTF-8 JSON serialization succeeds (rejects surrogates / NaN).
    format_json_bytes(record)


def write_comment(root: Path, record: dict[str, Any]) -> Path:
    """Validate ``record`` and atomically create ``<root>/records/comments/<id>.json``."""
    validate_comment_record(record, check_id=True)
    encoded = format_json_bytes(record)

    comments_dir = root / "records" / "comments"
    comments_dir.mkdir(parents=True, exist_ok=True)
    dest = comments_dir / f"{record['id']}.json"

    fd, tmp_name = tempfile.mkstemp(dir=comments_dir, prefix=f".{record['id']}.")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
        os.link(tmp_path, dest)
    finally:
        tmp_path.unlink(missing_ok=True)
    return dest


def load_comments(root: Path) -> list[dict[str, Any]]:
    """Load all valid comment records from ``<root>/records/comments/`` sorted by time and id."""
    comments_dir = root / "records" / "comments"
    if not comments_dir.is_dir():
        return []

    records: list[dict[str, Any]] = []
    for path in sorted(comments_dir.iterdir()):
        if path.name.startswith(".") or path.suffix != ".json" or not path.is_file():
            continue
        try:
            raw_text = path.read_bytes().decode("utf-8")
            data = json.loads(raw_text, parse_constant=_reject_json_constant)
            validate_comment_record(data, check_id=True)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        records.append(data)

    records.sort(key=lambda item: (item["created"], item["id"]))
    return records


def validate_span_bounds(start_val: Any, end_val: Any, duration: float) -> tuple[float, float]:
    """Validate finite numeric span bounds against ``0 <= start <= end <= duration + 0.05``."""
    if not (_is_finite_number(start_val) and _is_finite_number(end_val)):
        raise ValueError("span start and end must be finite numbers")

    raw_start = float(start_val)
    raw_end = float(end_val)
    max_end = duration + SPAN_TOLERANCE
    if raw_start < 0.0 or raw_start > raw_end or raw_end > max_end:
        raise ValueError(
            f"span {raw_start}:{raw_end} out of range for duration {duration:.3f}s "
            f"(require 0 <= start <= end <= {max_end:.3f})"
        )

    start = _clean_zero(round(raw_start, 3))
    end = _clean_zero(round(raw_end, 3))
    if start < 0.0 or start > end or end > max_end:
        raise ValueError(f"span {start}:{end} out of range after millisecond rounding")
    return start, end


def parse_comment_span(span_arg: str | None, duration: float) -> tuple[float, float]:
    """Parse ``A:B`` for a comment span, defaulting to the full take ``0..duration``."""
    if span_arg is None:
        return 0.0, _clean_zero(round(float(duration), 3))

    parts = span_arg.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid span {span_arg!r}: expected A:B in seconds")
    try:
        raw_start = float(parts[0])
        raw_end = float(parts[1])
    except ValueError as exc:
        raise ValueError(f"invalid span {span_arg!r}: non-numeric bound") from exc
    return validate_span_bounds(raw_start, raw_end, duration)


def build_comment_record(
    *,
    round_id: str,
    sha256: str,
    label_shown: str,
    start: float,
    end: float,
    kind: str,
    text: str,
    author: str,
    channel: str,
    answers: str | None = None,
    replaces: str | None = None,
    created: str | None = None,
) -> dict[str, Any]:
    """Assemble and validate a complete ``sal.comment/1`` record with computed ``id``."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if created is None else created
    record: dict[str, Any] = {
        "schema": "sal.comment/1",
        "id": "",
        "created": stamp,
        "round": round_id,
        "take": {"sha256": sha256, "label_shown": label_shown},
        "span": {"start": _clean_zero(round(start, 3)), "end": _clean_zero(round(end, 3))},
        "kind": kind,
        "text": text,
        "author": author,
        "channel": channel,
        "answers": answers,
        "replaces": replaces,
    }
    record["id"] = comment_id(record)
    validate_comment_record(record, check_id=True)
    return record


def load_round_context(round_dir: Path) -> tuple[str, dict[str, float], dict[str, str]]:
    """Load round id, take durations, and ``label_shown`` mappings for ``round_dir``."""
    round_json_path = round_dir / "round.json"
    if not round_json_path.is_file():
        raise ValueError(f"round file does not exist: {round_json_path}")
    try:
        round_data = json.loads(round_json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read round file {round_json_path}: {exc}") from exc

    if not isinstance(round_data, dict) or round_data.get("schema") != "sal.round/1":
        raise ValueError(f"invalid round schema in {round_json_path}")
    round_id = round_data.get("id")
    if not isinstance(round_id, str) or not ROUND_ID_RE.fullmatch(round_id):
        raise ValueError(f"invalid round id in {round_json_path}")
    takes = round_data.get("takes")
    if not isinstance(takes, list) or not takes:
        raise ValueError(f"invalid takes list in {round_json_path}")

    duration_by_sha: dict[str, float] = {}
    label_shown_by_sha: dict[str, str] = {}
    for take in takes:
        if not isinstance(take, dict):
            raise ValueError(f"invalid take entry in {round_json_path}")
        sha = take.get("sha256")
        dur = take.get("duration")
        label = take.get("label")
        if not isinstance(sha, str) or not SHA256_RE.fullmatch(sha):
            raise ValueError(f"invalid take sha256 in {round_json_path}")
        if not _is_finite_number(dur) or float(dur) <= 0:
            raise ValueError(f"invalid take duration in {round_json_path}")
        if not isinstance(label, str) or not label:
            raise ValueError(f"invalid take label in {round_json_path}")
        duration_by_sha[sha] = float(dur)
        label_shown_by_sha[sha] = label

    private_path = round_dir / "build.private.json"
    if private_path.is_file():
        try:
            private_data = json.loads(private_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read {private_path}: {exc}") from exc
        if isinstance(private_data, dict) and isinstance(private_data.get("takes"), list):
            for entry in private_data["takes"]:
                if isinstance(entry, dict):
                    sha = entry.get("sha256")
                    shown = entry.get("shown")
                    if (
                        isinstance(sha, str)
                        and sha in label_shown_by_sha
                        and isinstance(shown, str)
                        and shown
                    ):
                        label_shown_by_sha[sha] = shown

    return round_id, duration_by_sha, label_shown_by_sha


def filter_inbox_comments(
    comments: list[dict[str, Any]], *, round_id: str | None = None
) -> list[dict[str, Any]]:
    """Return unanswered, unreplaced operator comments ordered for ``sal inbox``."""
    answered_ids = {c["answers"] for c in comments if c["answers"] is not None}
    replaced_ids = {c["replaces"] for c in comments if c["replaces"] is not None}

    pending = [
        c
        for c in comments
        if c["author"] == "operator"
        and c["id"] not in answered_ids
        and c["id"] not in replaced_ids
        and (round_id is None or c["round"] == round_id)
    ]
    pending.sort(key=lambda c: (c["created"], c["id"]), reverse=True)
    pending.sort(key=lambda c: 0 if c["kind"] in ("question", "request") else 1)
    return pending
