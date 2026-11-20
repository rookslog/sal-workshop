import importlib.util
import random
import re
from pathlib import Path
from string import ascii_letters, digits
from types import ModuleType

import pytest
from conftest import REPO, WORKSHOP_EMAIL, git, make_repo, run_leak
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from hypothesis.strategies import DataObject

MEDIA_HOSTS = (
    "youtube.com",
    "youtu.be",
    "soundcloud.com",
    "bandcamp.com",
    "vimeo.com",
    "spotify.com",
    "music.apple.com",
    "mixcloud.com",
    "beatport.com",
    "drive.google.com",
    "dropbox.com",
    "wetransfer.com",
    "mega.nz",
)
EXAMPLE_DOMAIN = "example.org"
OTHER_EMAIL = "@".join(("other", EXAMPLE_DOMAIN))
HOME_PATH = "/".join(("", "home", "someone"))
DENYLIST_ENTRY = "Night Bus Seven"
SAFE_LINE_ALPHABET = ascii_letters + digits + " _-:,;()[]{}'\"=+*#"
SAFE_TEXT = st.text(alphabet=SAFE_LINE_ALPHABET + "\n")
SAFE_LINES = st.lists(st.text(alphabet=SAFE_LINE_ALPHABET), min_size=1)
NAME = st.text(alphabet=ascii_letters, min_size=1, max_size=12)


def test_b1_clean(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"a.txt": "one\n", "b.txt": "two\n"})
    result = run_leak(repo)

    assert result.returncode == 0
    assert result.stdout == "leak check: OK (2 files)\n"
    assert result.stderr == ""


def test_b2_users_path(tmp_path: Path) -> None:
    path = "/".join(("", "Users", "someone", "music"))
    repo = make_repo(tmp_path, {"notes.txt": f"notes\n{path}\n"})
    result = run_leak(repo)

    assert result.returncode == 1
    assert result.stderr == "notes.txt:2: home-path\n"
    assert result.stdout == ""


def test_b3_home_path(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"notes.txt": f"{HOME_PATH}\n"})
    result = run_leak(repo)

    assert result.returncode == 1
    assert result.stderr == "notes.txt:1: home-path\n"
    assert result.stdout == ""


def test_b4_home_prefix_only(tmp_path: Path) -> None:
    users_prefix = "/".join(("", "Users", ""))
    home_prefix = "/".join(("", "home", ""))
    text = f"paths under {users_prefix} are refused\nthe {home_prefix} directory\n"
    repo = make_repo(tmp_path, {"notes.txt": text})

    assert run_leak(repo).returncode == 0


@pytest.mark.parametrize("host", MEDIA_HOSTS)
def test_b5_media_hosts(tmp_path: Path, host: str) -> None:
    repo = make_repo(tmp_path, {"notes.txt": f"https://www.{host}/x\n"})
    result = run_leak(repo)

    assert result.returncode == 1
    assert result.stderr == "notes.txt:1: media-link\n"
    assert result.stdout == ""


def test_b5b_media_subdomain_http(tmp_path: Path) -> None:
    host = "bandcamp.com"
    repo = make_repo(tmp_path, {"notes.txt": f"http://artist.{host}/album/x\n"})
    result = run_leak(repo)

    assert result.returncode == 1
    assert result.stderr == "notes.txt:1: media-link\n"
    assert result.stdout == ""


def test_b5c_media_names_outside_host(tmp_path: Path) -> None:
    suffix = "soundcloud.com.html"
    repo = make_repo(
        tmp_path, {"notes.txt": f"youtube and dropbox\nhttps://example.org/{suffix}\n"}
    )

    assert run_leak(repo).returncode == 0


def test_b6a_other_email(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"notes.txt": f"someone@{EXAMPLE_DOMAIN}\n"})
    result = run_leak(repo)

    assert result.returncode == 1
    assert result.stderr == "notes.txt:1: email\n"
    assert result.stdout == ""


def test_b6b_workshop_email(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"notes.txt": f"{WORKSHOP_EMAIL}\n"})

    assert run_leak(repo).returncode == 0


def test_b6c_at_signs_outside_email(tmp_path: Path) -> None:
    text = "\n".join(
        (
            "      - uses: actions/checkout@v4",
            '@pytest.mark.parametrize("x", [1])',
            "y = a @ b.T",
            "",
        )
    )
    repo = make_repo(tmp_path, {"notes.txt": text})

    assert run_leak(repo).returncode == 0


@pytest.fixture
def denylist_repo(tmp_path: Path) -> tuple[Path, Path]:
    denylist = tmp_path / "denylist.txt"
    denylist.write_text(f"{DENYLIST_ENTRY}\n\n", encoding="utf-8")
    repo = make_repo(tmp_path / "repo", {"notes.txt": f"one\ntwo\n{DENYLIST_ENTRY.lower()}\n"})
    return repo, denylist


def test_b7a_denylist_line(denylist_repo: tuple[Path, Path]) -> None:
    repo, denylist = denylist_repo
    result = run_leak(repo, denylist=denylist)

    assert result.returncode == 1
    assert DENYLIST_ENTRY.casefold() not in result.stderr.casefold()
    assert result.stderr == "notes.txt:3: denylist\n"
    assert result.stdout == ""


def test_b7b_denylist_unset(denylist_repo: tuple[Path, Path]) -> None:
    repo, _ = denylist_repo

    assert run_leak(repo).returncode == 0


def test_b7c_denylist_filename(tmp_path: Path) -> None:
    denylist = tmp_path / "denylist.txt"
    denylist.write_text(f"{DENYLIST_ENTRY}\n", encoding="utf-8")
    repo = make_repo(tmp_path / "repo", {"Night Bus Seven.txt": "clean\n"})
    result = run_leak(repo, denylist=denylist)

    assert result.returncode == 1
    assert result.stderr == "Night Bus Seven.txt:0: denylist\n"
    assert result.stdout == ""


def test_b7d_missing_denylist(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"notes.txt": "clean\n"})
    result = run_leak(repo, denylist=tmp_path / "missing.txt")

    assert result.returncode == 2
    assert "SAL_DENYLIST" in result.stderr
    assert "leak check: OK" not in result.stdout + result.stderr


def test_b8a_commit_author_email(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"notes.txt": "clean\n"}, email=OTHER_EMAIL)
    git(repo, "config", "user.email", WORKSHOP_EMAIL)
    result = run_leak(repo)

    assert result.returncode == 1
    assert OTHER_EMAIL not in result.stderr
    assert re.fullmatch(r"commit [0-9a-f]{7,40}:0: identity\n", result.stderr)
    assert result.stdout == ""


def test_b8b_config_email(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"notes.txt": "clean\n"})
    git(repo, "config", "user.email", OTHER_EMAIL)
    result = run_leak(repo)

    assert result.returncode == 1
    assert result.stderr == "config:0: identity\n"
    assert result.stdout == ""


def test_b8c_commit_committer_email(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"notes.txt": "clean\n"})
    git(repo, "commit", "--amend", "--no-edit", committer_email=OTHER_EMAIL)
    result = run_leak(repo)

    assert result.returncode == 1
    assert re.fullmatch(r"commit [0-9a-f]{7,40}:0: identity\n", result.stderr)
    assert result.stdout == ""


def test_b8d_no_commits(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "main")
    (tmp_path / "notes.txt").write_text("clean\n", encoding="utf-8")
    git(tmp_path, "add", "notes.txt")
    result = run_leak(tmp_path)

    assert result.returncode == 0
    assert result.stdout == "leak check: OK (1 files)\n"
    assert "Traceback" not in result.stderr


def test_b9_only_tracked_files(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"notes.txt": "clean\n", ".gitignore": "ignored.txt\n"})
    (repo / "untracked.txt").write_text(f"{HOME_PATH}\n", encoding="utf-8")
    (repo / "ignored.txt").write_text(f"{HOME_PATH}\n", encoding="utf-8")
    result = run_leak(repo)

    assert result.returncode == 0
    assert result.stdout == "leak check: OK (2 files)\n"


@pytest.fixture
def binary_data() -> bytes:
    return random.Random(7).randbytes(2_000).translate(bytes.maketrans(b"@/", b"\0\0"))


def test_b10_clean_binary(tmp_path: Path, binary_data: bytes) -> None:
    repo = make_repo(tmp_path, {"blob.bin": binary_data})
    result = run_leak(repo)

    assert result.returncode == 0
    assert "Traceback" not in result.stdout + result.stderr


def test_b10b_binary_with_home_path(tmp_path: Path, binary_data: bytes) -> None:
    middle = len(binary_data) // 2
    content = binary_data[:middle] + HOME_PATH.encode("utf-8") + binary_data[middle:]
    repo = make_repo(tmp_path, {"blob.bin": content})
    result = run_leak(repo)

    assert result.returncode == 1
    assert re.fullmatch(r"blob\.bin:[1-9][0-9]*: home-path\n", result.stderr)
    assert result.stdout == ""


def test_b11_findings_sorted(tmp_path: Path) -> None:
    host = MEDIA_HOSTS[0]
    repo = make_repo(
        tmp_path,
        {
            "b.txt": f"{OTHER_EMAIL}\ntwo\nthree\nfour\n{HOME_PATH}\n",
            "a.txt": f"one\nhttps://www.{host}/x\n",
            "c.txt": "clean\n",
        },
    )
    result = run_leak(repo)

    assert result.returncode == 1
    assert result.stderr == "a.txt:2: media-link\nb.txt:1: email\nb.txt:5: home-path\n"
    assert result.stdout == ""


def test_b12_checker_and_tests_are_clean(tmp_path: Path) -> None:
    checker = REPO / "tools" / "leak_check.py"
    files = {str(checker.relative_to(REPO)): checker.read_bytes()}
    files.update(
        (str(path.relative_to(REPO)), path.read_bytes())
        for path in (REPO / "tests").rglob("*.py")
        if path.is_file()
    )
    repo = make_repo(tmp_path, files)
    result = run_leak(repo)

    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def leak_check() -> ModuleType:
    spec = importlib.util.spec_from_file_location("leak_check", REPO / "tools" / "leak_check.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def insert_on_line(lines: list[str], token: str, data: DataObject) -> tuple[int, str]:
    index = data.draw(st.integers(min_value=0, max_value=len(lines) - 1), label="line index")
    line = lines[index]
    position = data.draw(st.integers(min_value=0, max_value=len(line)), label="position")
    with_token = line[:position] + token + line[position:]
    return index + 1, "\n".join([*lines[:index], with_token, *lines[index + 1 :]])


@settings(max_examples=200, deadline=None, derandomize=True)
@given(text=SAFE_TEXT)
def test_p1_clean_text(leak_check: ModuleType, text: str) -> None:
    assert leak_check.scan_text(text) == []


@settings(max_examples=200, deadline=None, derandomize=True)
@given(lines=SAFE_LINES, name=NAME, data=st.data())
def test_p2_inserted_home_path(
    leak_check: ModuleType, lines: list[str], name: str, data: DataObject
) -> None:
    path = "/".join(("", "home", name))
    line_number, text = insert_on_line(lines, path, data)
    findings = leak_check.scan_text(text)

    assert (line_number, "home-path") in findings
    assert all(line == line_number for line, _ in findings)


@settings(max_examples=200, deadline=None, derandomize=True)
@given(lines=SAFE_LINES, name=NAME, data=st.data())
def test_p3_inserted_email(
    leak_check: ModuleType, lines: list[str], name: str, data: DataObject
) -> None:
    # Keep the generated address separate from neighbouring letters and punctuation.
    line_number, text = insert_on_line(lines, f" {name}@{EXAMPLE_DOMAIN} ", data)

    assert leak_check.scan_text(text) == [(line_number, "email")]


@settings(max_examples=200, deadline=None, derandomize=True)
@given(
    lines=SAFE_LINES,
    entry=st.text(ascii_letters + " ", min_size=4, max_size=20),
    data=st.data(),
)
def test_p4_inserted_denylist_entry(
    leak_check: ModuleType, lines: list[str], entry: str, data: DataObject
) -> None:
    entry = entry.strip()
    assume(sum(letter in ascii_letters for letter in entry) >= 4)
    assume(entry.casefold() not in "\n".join(lines).casefold())
    case = data.draw(st.lists(st.booleans(), min_size=len(entry), max_size=len(entry)))
    token = "".join(
        letter.upper() if upper else letter.lower()
        for letter, upper in zip(entry, case, strict=True)
    )
    line_number, text = insert_on_line(lines, token, data)

    assert (line_number, "denylist") in leak_check.scan_text(text, [entry])
    assert leak_check.scan_text(text, []) == []


@settings(max_examples=200, deadline=None, derandomize=True)
@given(text=SAFE_TEXT)
def test_p5_deterministic_sorted_findings(leak_check: ModuleType, text: str) -> None:
    text = f"{text}\n{HOME_PATH} {OTHER_EMAIL} {HOME_PATH}\n{OTHER_EMAIL}\n"
    findings = leak_check.scan_text(text)

    assert findings == leak_check.scan_text(text)
    assert findings == sorted(set(findings))
