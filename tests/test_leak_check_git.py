import re
from pathlib import Path

from conftest import git, make_repo, run_leak

HOME_PATH = "/".join(("", "home", "sal-fixture-missing"))
COMMIT = r"commit [0-9a-f]{7,40}"


def _remove_in_commit(repo: Path, filename: str) -> None:
    git(repo, "rm", "--", filename)
    git(repo, "commit", "-m", "Remove fixture file")


def test_k1_head_content_survives_worktree_scrub(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"notes.txt": f"{HOME_PATH}\n"})
    (repo / "notes.txt").write_text("clean\n", encoding="utf-8")

    result = run_leak(repo)

    assert result.returncode == 1, result
    assert "notes.txt:1: home-path" in result.stderr.splitlines()


def test_k2_staged_content_survives_worktree_scrub(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"notes.txt": "clean\n"})
    draft = repo / "draft.txt"
    email = "@".join(("other", "example.org"))
    draft.write_text(f"{email}\n", encoding="utf-8")
    git(repo, "add", "--", draft.name)
    draft.write_text("clean\n", encoding="utf-8")

    result = run_leak(repo)

    assert result.returncode == 1, result
    assert "draft.txt:1: email" in result.stderr.splitlines()


def test_k3_deleted_home_path_is_found_in_history(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"old.txt": f"first line\n{HOME_PATH}\n"})
    _remove_in_commit(repo, "old.txt")

    result = run_leak(repo)

    assert result.returncode == 1, result
    assert re.fullmatch(rf"{COMMIT}:old\.txt:2: home-path\n?", result.stderr)


def test_k4_deleted_media_is_found_in_history(tmp_path: Path) -> None:
    filename = ".".join(("clip", "mp3"))
    repo = make_repo(tmp_path, {filename: "not really media\n"})
    _remove_in_commit(repo, filename)

    result = run_leak(repo)

    assert result.returncode == 1, result
    assert any(
        re.fullmatch(rf"{COMMIT}:{re.escape(filename)}:0: media-file", line)
        for line in result.stderr.splitlines()
    )


def test_k5_dangling_symlink_target_is_checked(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"notes.txt": "clean\n"})
    target = Path(HOME_PATH)
    assert not target.exists()
    (repo / "link").symlink_to(target)
    git(repo, "add", "--", "link")
    git(repo, "commit", "-m", "Add fixture link")

    result = run_leak(repo)

    assert result.returncode == 1, result
    assert "link:0: home-path" in result.stderr.splitlines()
    assert "no such file" not in result.stderr.lower()


def test_k6_clean_history_is_allowed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"notes.txt": "first\n"})
    notes = repo / "notes.txt"
    notes.write_text("second\n", encoding="utf-8")
    git(repo, "add", "--", notes.name)
    notes.write_text("third\n", encoding="utf-8")
    git(repo, "add", "--", notes.name)
    git(repo, "commit", "-m", "Update notes twice")
    (repo / "more.txt").write_text("more notes\n", encoding="utf-8")
    git(repo, "add", "--", "more.txt")
    git(repo, "commit", "-m", "Add notes")

    result = run_leak(repo)

    assert result.returncode == 0, result


def test_k7_deleted_denylist_entry_is_found_in_history(tmp_path: Path) -> None:
    entry = " ".join(("Night", "Bus", "Seven"))
    denylist = tmp_path / "denylist.txt"
    denylist.write_text(f"{entry}\n", encoding="utf-8")
    repo = make_repo(tmp_path / "repo", {"old.txt": f"first line\n{entry.lower()}\n"})
    _remove_in_commit(repo, "old.txt")

    assert run_leak(repo).returncode == 0
    blocked = run_leak(repo, denylist=denylist)

    assert blocked.returncode == 1, blocked
    assert any(
        re.fullmatch(rf"{COMMIT}:old\.txt:2: denylist", line)
        for line in blocked.stderr.splitlines()
    )
