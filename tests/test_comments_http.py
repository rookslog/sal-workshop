import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from stat import S_IMODE
from threading import Barrier

import pytest
from conftest import (
    assert_schema,
    comment_files,
    comment_paths,
    comment_record,
    http,
    read_json,
    served,
)


def _sha256(round_dir: Path, index: int = 0) -> str:
    return read_json(round_dir / "round.json")["takes"][index]["sha256"]


def _body(sha256: str, **changes: object) -> dict:
    return {
        "sha256": sha256,
        "start": 3.0,
        "end": 5.0,
        "kind": "question",
        "text": "  why here?  ",
        **changes,
    }


def _post(url: str, body: dict | bytes) -> tuple[int, bytes]:
    if not isinstance(body, bytes):
        body = json.dumps(body).encode("utf-8")
    status, _, response = http("POST", url + "comments", body, {"Content-Type": "application/json"})
    return status, response


def _post_record(url: str, body: dict) -> dict:
    status, response = _post(url, body)
    assert status == 201, response
    return json.loads(response)


def _get_records(url: str) -> list[dict]:
    status, _, response = http("GET", url + "comments")
    assert status == 200, response
    records = json.loads(response)
    assert isinstance(records, list)
    return records


def test_h1_post_question(comment_round: Path) -> None:
    sha256 = _sha256(comment_round)
    with served(comment_round) as (url, root):
        record = _post_record(url, _body(sha256))
        assert comment_files(root) == [record]
    assert_schema("comment-1", record)
    assert record["round"] == comment_round.name
    assert record["author"] == "operator"
    assert record["channel"] == "page"
    assert record["span"] == {"start": 3.0, "end": 5.0}
    assert record["kind"] == "question"
    assert record["text"] == "  why here?  "
    assert record["answers"] is None
    assert record["replaces"] is None
    assert record["take"] == {"sha256": sha256, "label_shown": "zebra-first"}
    created = datetime.fromisoformat(record["created"])
    assert abs((datetime.now(UTC) - created).total_seconds()) <= 120


def test_h2_blind_label(blind_comment_round: Path) -> None:
    private = read_json(blind_comment_round / "build.private.json")
    sha256 = next(take["sha256"] for take in private["takes"] if take["shown"] == "B")
    with served(blind_comment_round) as (url, _):
        record = _post_record(url, _body(sha256))
    assert record["take"] == {"sha256": sha256, "label_shown": "B"}


def test_h3_get_sorted_records(comment_round: Path) -> None:
    first, second = (_sha256(comment_round, index) for index in range(2))
    with served(comment_round) as (url, _):
        posted = [
            _post_record(url, _body(first, text="first question")),
            _post_record(url, _body(first, text="second question")),
            _post_record(url, _body(second, text="third question")),
        ]
        records = _get_records(url)
    assert records == sorted(posted, key=lambda record: (record["created"], record["id"]))


@pytest.mark.parametrize(
    ("invalid", "statuses"),
    [
        pytest.param(b"not json", (400,), id="not-json"),
        pytest.param(b"[1, 2]", (400,), id="not-object"),
        pytest.param(None, (400,), id="missing-text"),
        pytest.param({"author": "operator"}, (400,), id="extra-author"),
        pytest.param({"sha256": "0" * 64}, (400,), id="unknown-take"),
        pytest.param({"start": 5, "end": 3}, (400,), id="reversed-span"),
        pytest.param({"start": -1}, (400,), id="negative-start"),
        pytest.param({"end": 99}, (400,), id="past-end"),
        pytest.param({"kind": "answer"}, (400,), id="answer-kind"),
        pytest.param({"kind": "shout"}, (400,), id="unknown-kind"),
        pytest.param({"text": 5}, (400,), id="numeric-text"),
        pytest.param({"kind": "question", "text": "   "}, (400,), id="blank-question"),
        pytest.param({"text": "x" * 70_000}, (400, 413), id="oversized"),
    ],
)
def test_h4_bad_bodies(
    comment_round: Path, invalid: bytes | dict | None, statuses: tuple[int, ...]
) -> None:
    body: dict | bytes
    if isinstance(invalid, bytes):
        body = invalid
    else:
        body = {**_body(_sha256(comment_round)), **(invalid or {})}
        if invalid is None:
            del body["text"]
    with served(comment_round) as (url, root):
        status, response = _post(url, body)
        assert status in statuses, response
        assert comment_paths(root) == []


def test_h5_empty_flag_at_moment(comment_round: Path) -> None:
    body = _body(_sha256(comment_round), kind="flag", text="", start=7.0, end=7.0)
    with served(comment_round) as (url, _):
        record = _post_record(url, body)
    assert record["span"] == {"start": 7.0, "end": 7.0}


def test_h6_ignores_other_round(comment_round: Path) -> None:
    from sal.comments import comment_id, write_comment

    record = comment_record(round_id="other", sha256=_sha256(comment_round))
    record["id"] = comment_id(record)
    with served(comment_round) as (url, root):
        write_comment(root, record)
        assert record not in _get_records(url)


def test_h7_survives_restart(comment_round: Path) -> None:
    with served(comment_round) as (url, root):
        record = _post_record(url, _body(_sha256(comment_round)))
        (path,) = comment_paths(root)
        original = path.read_bytes()
    with served(comment_round) as (url, _):
        assert _get_records(url) == [record]
        assert path.read_bytes() == original


def test_h8_concurrent_posts(comment_round: Path) -> None:
    sha256 = _sha256(comment_round)
    start = Barrier(20, timeout=5)
    with served(comment_round) as (url, root):

        def post_one(index: int) -> int:
            start.wait()
            return _post(url, _body(sha256, text=f"n{index}"))[0]

        with ThreadPoolExecutor(max_workers=20) as threads:
            statuses = list(threads.map(post_one, range(20)))
        assert statuses == [201] * 20
        assert len(comment_paths(root)) == 20
        records = _get_records(url)
        assert len(records) == 20
        assert len({record["id"] for record in records}) == 20


def test_h9_read_only_records_refuse_save(comment_round: Path) -> None:
    sha256 = _sha256(comment_round)
    with served(comment_round) as (url, root):
        _post_record(url, _body(sha256))
        (path,) = comment_paths(root)
        original = path.read_bytes()
        mode = S_IMODE(path.parent.stat().st_mode)
        try:
            path.parent.chmod(0o555)
            status, response = _post(url, _body(sha256, text="second question"))
            assert status in (500, 503), response
        finally:
            path.parent.chmod(mode)
        assert comment_paths(root) == [path]
        assert path.read_bytes() == original
