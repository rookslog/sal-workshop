import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

import pytest
from conftest import assert_schema, comment_paths, comment_record
from hypothesis import given, settings
from hypothesis import strategies as st


def _fixed_json(record: dict) -> bytes:
    text = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def test_u1_comment_id() -> None:
    from sal.comments import comment_id

    record = comment_record()
    expected_hash = hashlib.sha256(_fixed_json(record)).hexdigest()[:6]
    record["id"] = comment_id(record)
    assert re.fullmatch(r"c-\d{8}T\d{6}Z-[0-9a-f]{6}", record["id"])
    assert record["id"].split("-")[1] == record["created"].replace("-", "").replace(":", "")
    assert record["id"][-6:] == expected_hash


def test_u2_write_fixed_json(tmp_path: Path) -> None:
    from sal.comments import comment_id, write_comment

    record = comment_record()
    record["id"] = comment_id(record)
    path = write_comment(tmp_path, record)
    assert path == tmp_path / "records" / "comments" / f"{record['id']}.json"
    assert path.read_bytes() == _fixed_json(record)
    assert_schema("comment-1", json.loads(path.read_bytes()))


def test_u3_no_overwrite(tmp_path: Path) -> None:
    from sal.comments import comment_id, write_comment

    record = comment_record()
    record["id"] = comment_id(record)
    path = write_comment(tmp_path, record)
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_comment(tmp_path, record)
    assert path.read_bytes() == original


def test_u4_mismatched_id(tmp_path: Path) -> None:
    from sal.comments import comment_id, write_comment

    record = comment_record()
    record["id"] = comment_id(record)
    record["text"] = "changed text"
    with pytest.raises(ValueError):
        write_comment(tmp_path, record)
    assert comment_paths(tmp_path) == []


@pytest.mark.parametrize(
    ("key", "value"),
    [("kind", "shout"), ("author", "someone"), ("text", None)],
    ids=["kind", "author", "missing-key"],
)
def test_u5_schema_refusal(tmp_path: Path, key: str, value: str | None) -> None:
    from sal.comments import write_comment

    record = comment_record()
    if value is None:
        del record[key]
    else:
        record[key] = value
    timestamp = record["created"].replace("-", "").replace(":", "")
    record["id"] = f"c-{timestamp}-{hashlib.sha256(_fixed_json(record)).hexdigest()[:6]}"
    with pytest.raises(ValueError):
        write_comment(tmp_path, record)
    assert comment_paths(tmp_path) == []


@pytest.mark.parametrize(
    "text",
    [
        "  two spaces each side  ",
        "line one\nline two\r\n",
        "tab\there",
        "naïve — “quoted” 日本語 🎧",
        "",
    ],
    ids=["spaces", "line-endings", "tab", "unicode", "empty-flag"],
)
def test_u6_text_round_trip(tmp_path: Path, text: str) -> None:
    from sal.comments import comment_id, load_comments, write_comment

    record = comment_record(text=text)
    record["id"] = comment_id(record)
    write_comment(tmp_path, record)
    assert load_comments(tmp_path)[0]["text"] == text


def test_u7_load_ignores_invalid_files(tmp_path: Path) -> None:
    from sal.comments import comment_id, load_comments, write_comment

    record = comment_record()
    record["id"] = comment_id(record)
    path = write_comment(tmp_path, record)
    (path.parent / "junk.json").write_text("{not json", encoding="utf-8")
    (path.parent / "other.json").write_text('{"schema": "something/else"}', encoding="utf-8")
    assert load_comments(tmp_path) == [record]


def test_u8_load_missing_folder(tmp_path: Path) -> None:
    from sal.comments import load_comments

    assert not (tmp_path / "records" / "comments").exists()
    assert load_comments(tmp_path) == []


@settings(max_examples=200, deadline=None)
@given(text=st.text(max_size=300))
def test_u9_unicode_round_trip(text: str) -> None:
    from sal.comments import comment_id, load_comments, write_comment

    record = comment_record(text=text)
    record["id"] = comment_id(record)
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = write_comment(root, record)
        assert json.loads(path.read_bytes().decode("utf-8")) == record
        loaded = load_comments(root)[0]
        assert loaded["text"] == text
        assert comment_id(loaded) == loaded["id"]


def test_u11_concurrent_writes(tmp_path: Path) -> None:
    from sal.comments import comment_id, write_comment

    record = comment_record()
    record["id"] = comment_id(record)
    start = Barrier(20, timeout=5)

    def write_once() -> Path | FileExistsError:
        start.wait()
        try:
            return write_comment(tmp_path, record.copy())
        except FileExistsError as error:
            return error

    with ThreadPoolExecutor(max_workers=20) as threads:
        results = list(threads.map(lambda _: write_once(), range(20)))
    paths = [result for result in results if isinstance(result, Path)]
    assert len(paths) == 1
    assert sum(isinstance(result, FileExistsError) for result in results) == 19
    assert_schema("comment-1", json.loads(paths[0].read_bytes()))
