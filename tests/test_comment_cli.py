import json
from pathlib import Path
from subprocess import CompletedProcess
from time import sleep

import pytest
from conftest import comment_files, comment_paths, comment_record, read_json, run_sal

UNKNOWN_ID = "c-20200101T000000Z-aaaaaa"


def _take(round_dir: Path) -> dict:
    return read_json(round_dir / "round.json")["takes"][0]


def _add(
    round_dir: Path,
    *,
    round_id: str = "r1",
    sha256: str | None = None,
    span: str | None = "3:5",
    kind: str = "verdict",
    text: str = "this one",
    extra: tuple[str, ...] = (),
) -> CompletedProcess[str]:
    args = [
        "comment",
        "add",
        "--round",
        round_id,
        "--take",
        sha256 or _take(round_dir)["sha256"],
        "--kind",
        kind,
        "--text",
        text,
    ]
    if span is not None:
        args.extend(["--span", span])
    return run_sal(*args, *extra, cwd=round_dir.parent.parent)


def _add_record(round_dir: Path, **options) -> dict:
    result = _add(round_dir, **options)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    output = json.loads(lines[0])
    comment_id = output["comment"]
    assert output == {"comment": comment_id}
    root = round_dir.parent.parent
    record = read_json(root / "records" / "comments" / f"{comment_id}.json")
    assert record["id"] == comment_id
    return record


def _inbox(root: Path, *options: str) -> list[dict]:
    result = run_sal("inbox", *options, "--json", cwd=root)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    records = json.loads(lines[0])
    assert isinstance(records, list)
    return records


def test_k1_add_verdict(comment_round: Path) -> None:
    record = _add_record(comment_round)
    sha256 = _take(comment_round)["sha256"]
    private = read_json(comment_round / "build.private.json")
    shown = next(take["shown"] for take in private["takes"] if take["sha256"] == sha256)
    assert record["author"] == "operator"
    assert record["channel"] == "chat-transcribed"
    assert record["span"] == {"start": 3.0, "end": 5.0}
    assert record["take"]["label_shown"] == shown


def test_k2_defaults_to_whole_take(comment_round: Path) -> None:
    record = _add_record(comment_round, span=None)
    assert record["span"] == {"start": 0.0, "end": _take(comment_round)["duration"]}


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"round_id": "nosuch"}, id="unknown-round"),
        pytest.param({"sha256": "0" * 64}, id="unknown-take"),
        pytest.param({"span": "5:3"}, id="bad-span"),
        pytest.param({"kind": "shout"}, id="bad-kind"),
        pytest.param({"kind": "question", "text": ""}, id="empty-question"),
        pytest.param({"extra": ("--answers", UNKNOWN_ID)}, id="missing-answers"),
        pytest.param({"extra": ("--channel", "page")}, id="bad-channel"),
    ],
)
def test_k3_add_refusals(comment_round: Path, options: dict) -> None:
    result = _add(comment_round, **options)
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip()
    assert comment_paths(comment_round.parent.parent) == []


def test_k4_answer_inherits_context(comment_round: Path) -> None:
    root = comment_round.parent.parent
    question = _add_record(comment_round, kind="question", text="why here?")
    author = "agent:codex/gpt-6.2-sol"
    result = run_sal(
        "comment",
        "answer",
        question["id"],
        "--text",
        "because of the cut",
        "--author",
        author,
        cwd=root,
    )
    assert result.returncode == 0, result.stderr

    (answer,) = [record for record in comment_files(root) if record["id"] != question["id"]]
    assert answer["kind"] == "answer"
    assert answer["channel"] == "agent"
    assert answer["author"] == author
    assert answer["answers"] == question["id"]
    for key in ("round", "take", "span"):
        assert answer[key] == question[key]


@pytest.mark.parametrize("invalid", ["unknown-id", "wrong-author", "empty-text"])
def test_k5_answer_refusals(comment_round: Path, invalid: str) -> None:
    root = comment_round.parent.parent
    question = _add_record(comment_round, kind="question", text="why here?")
    original = comment_paths(root)
    result = run_sal(
        "comment",
        "answer",
        UNKNOWN_ID if invalid == "unknown-id" else question["id"],
        "--author",
        "someone" if invalid == "wrong-author" else "agent:x/y",
        "--text",
        "" if invalid == "empty-text" else "because of the cut",
        cwd=root,
    )
    assert result.returncode != 0
    assert comment_paths(root) == original


@pytest.fixture
def inbox_comments(comment_round: Path) -> dict[str, dict]:
    records = {}
    for index, kind in enumerate(("flag", "question", "verdict", "request")):
        if index:
            sleep(1.1)
        records[kind] = _add_record(comment_round, kind=kind)
    return records


def test_k6_inbox_order(comment_round: Path, inbox_comments: dict[str, dict]) -> None:
    records = _inbox(comment_round.parent.parent, "--round", "r1")
    assert records == [inbox_comments[kind] for kind in ("request", "question", "verdict", "flag")]


def test_k7_answered_question_leaves_inbox(
    comment_round: Path, inbox_comments: dict[str, dict]
) -> None:
    root = comment_round.parent.parent
    result = run_sal(
        "comment",
        "answer",
        inbox_comments["question"]["id"],
        "--text",
        "because of the cut",
        "--author",
        "agent:x/y",
        cwd=root,
    )
    assert result.returncode == 0, result.stderr
    assert _inbox(root) == [inbox_comments[kind] for kind in ("request", "verdict", "flag")]


def test_k8_replaced_comment_leaves_inbox(comment_round: Path) -> None:
    first = _add_record(comment_round, kind="flag", text="A")
    second = _add_record(
        comment_round, kind="flag", text="replacement", extra=("--replaces", first["id"])
    )
    ids = [record["id"] for record in _inbox(comment_round.parent.parent)]
    assert first["id"] not in ids
    assert second["id"] in ids


def test_k9_agent_record_not_in_inbox(comment_round: Path) -> None:
    from sal.comments import comment_id, write_comment

    root = comment_round.parent.parent
    record = comment_record(sha256=_take(comment_round)["sha256"])
    record.update(author="agent:x/y", channel="agent")
    record["id"] = comment_id(record)
    write_comment(root, record)
    assert record["id"] not in [entry["id"] for entry in _inbox(root)]


def test_k10_inbox_plain_text(comment_round: Path, inbox_comments: dict[str, dict]) -> None:
    result = run_sal("inbox", cwd=comment_round.parent.parent)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 4
    for line, kind in zip(lines, ("request", "question", "verdict", "flag"), strict=True):
        assert inbox_comments[kind]["id"] in line


def test_k11_unknown_round_empty_inbox(comment_round: Path) -> None:
    assert _inbox(comment_round.parent.parent, "--round", "nosuch") == []
