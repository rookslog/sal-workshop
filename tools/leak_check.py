"""Check a Git repository for leaked paths, links, emails, denylist entries, and identity."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

_HOME_PREFIXES = ("/" + "Users" + "/", "/" + "home" + "/")
_HOME_PATH_RE = re.compile(
    r"(?:" + "|".join(re.escape(prefix) for prefix in _HOME_PREFIXES) + r")[^\s/]+"
)

_MEDIA_HOST_PARTS = (
    ("youtube", "com"),
    ("youtu", "be"),
    ("soundcloud", "com"),
    ("bandcamp", "com"),
    ("vimeo", "com"),
    ("spotify", "com"),
    ("music", "apple", "com"),
    ("mixcloud", "com"),
    ("beatport", "com"),
    ("drive", "google", "com"),
    ("dropbox", "com"),
    ("wetransfer", "com"),
    ("mega", "nz"),
)
_MEDIA_HOSTS = tuple(".".join(parts) for parts in _MEDIA_HOST_PARTS)
_URL_RE = re.compile(r"(?i)(?<![A-Za-z0-9])htt" + r"ps?://([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)")

_WORKSHOP_EMAIL = "@".join(("workshop", "sal.invalid"))
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+" + "@" + r"(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![A-Za-z0-9-]|\.[A-Za-z0-9-])"
)

_MAX_FILE_BYTES = 524_288
_MEDIA_EXTENSIONS = (
    ".wav",
    ".aif",
    ".aiff",
    ".flac",
    ".mp3",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".m4v",
    ".avi",
)
_MEDIA_PREFIXES = (b"fLaC", b"ID3", b"OggS", b"\x1a\x45\xdf\xa3")


def _normalize_denylist(denylist: Sequence[str]) -> tuple[str, ...]:
    return tuple(entry.strip().casefold() for entry in denylist if entry.strip())


def scan_text(text: str, denylist: Sequence[str] = ()) -> list[tuple[int, str]]:
    """Scan one file's text and return sorted, unique ``(line, rule)`` pairs."""
    normalized_deny = _normalize_denylist(denylist)
    findings: set[tuple[int, str]] = set()

    for line_no, line in enumerate(text.split("\n"), start=1):
        if _HOME_PATH_RE.search(line):
            findings.add((line_no, "home-path"))

        for match in _URL_RE.finditer(line):
            host = match.group(1).lower()
            if any(host == target or host.endswith(f".{target}") for target in _MEDIA_HOSTS):
                findings.add((line_no, "media-link"))
                break

        for match in _EMAIL_RE.finditer(line):
            if match.group(0).lower() != _WORKSHOP_EMAIL:
                findings.add((line_no, "email"))
                break

        if normalized_deny:
            line_cf = line.casefold()
            if any(entry in line_cf for entry in normalized_deny):
                findings.add((line_no, "denylist"))

    return sorted(findings)


def _load_denylist() -> list[str]:
    denylist_env = os.environ.get("SAL_DENYLIST")
    if not denylist_env:
        return []
    denylist_path = Path(denylist_env)
    try:
        content = denylist_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read SAL_DENYLIST file: {exc}") from exc
    return [line.strip() for line in content.splitlines() if line.strip()]


def _has_head(repo: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", "HEAD"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _list_head_entries(repo: Path) -> list[tuple[str, str, str]]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-z", "HEAD"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git ls-tree failed: {message}")
    entries: list[tuple[str, str, str]] = []
    for raw in result.stdout.split(b"\x00"):
        if not raw:
            continue
        meta, sep, raw_path = raw.partition(b"\t")
        if not sep:
            raise RuntimeError("unexpected git ls-tree output format")
        mode, obj_type, sha = meta.decode("ascii").split()
        if obj_type != "blob":
            continue
        rel_path = raw_path.decode("utf-8", errors="surrogateescape")
        entries.append((rel_path, mode, sha))
    return entries


def _list_index_entries(repo: Path) -> list[tuple[str, str, str]]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-s", "-z"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git ls-files failed: {message}")
    entries: list[tuple[str, str, str]] = []
    for raw in result.stdout.split(b"\x00"):
        if not raw:
            continue
        meta, sep, raw_path = raw.partition(b"\t")
        if not sep:
            raise RuntimeError("unexpected git ls-files -s output format")
        mode, sha, _stage = meta.decode("ascii").split()
        if mode == "160000":
            continue
        rel_path = raw_path.decode("utf-8", errors="surrogateescape")
        entries.append((rel_path, mode, sha))
    return entries


def _list_history_entries(repo: Path, has_head: bool) -> list[tuple[str, str, str, str]]:
    rev_args = ["--all", "HEAD"] if has_head else ["--all"]
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "log",
            *rev_args,
            "--raw",
            "--no-abbrev",
            "--no-renames",
            "-m",
            "-z",
            "--format=__COMMIT__%H",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git log failed: {message}")
    entries: list[tuple[str, str, str, str]] = []
    tokens = iter(result.stdout.split(b"\x00"))
    current_commit: str | None = None
    for raw_tok in tokens:
        if not raw_tok:
            continue
        if raw_tok.startswith(b"__COMMIT__"):
            current_commit = raw_tok[len(b"__COMMIT__") :].decode("ascii")
            continue
        stripped = raw_tok.lstrip(b"\n")
        if stripped.startswith(b":"):
            parts = stripped[1:].decode("ascii").split()
            if len(parts) < 5:
                raise RuntimeError("unexpected git log --raw output format")
            new_mode = parts[1]
            new_sha = parts[3]
            raw_path = next(tokens, None)
            if raw_path is None:
                raise RuntimeError("unexpected git log --raw path")
            if current_commit is None:
                raise RuntimeError("git log --raw entry without commit")
            if new_mode in ("000000", "160000") or set(new_sha) == {"0"}:
                continue
            rel_path = raw_path.decode("utf-8", errors="surrogateescape")
            entries.append((current_commit, rel_path, new_mode, new_sha))
    return entries


def _read_blobs(repo: Path, shas: Sequence[str]) -> dict[str, bytes]:
    unique_shas = list(dict.fromkeys(shas))
    if not unique_shas:
        return {}
    stdin_bytes = ("\n".join(unique_shas) + "\n").encode("ascii")
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input=stdin_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git cat-file failed: {message}")
    blobs: dict[str, bytes] = {}
    buf = result.stdout
    pos = 0
    total = len(buf)
    while pos < total:
        nl = buf.find(b"\n", pos)
        if nl == -1:
            raise RuntimeError("unexpected git cat-file --batch output")
        header = buf[pos:nl].decode("ascii", errors="replace").split()
        pos = nl + 1
        if len(header) == 2 and header[1] == "missing":
            continue
        if len(header) != 3:
            raise RuntimeError("unexpected git cat-file --batch header")
        obj_sha, obj_type, size_str = header
        size = int(size_str)
        data = buf[pos : pos + size]
        if len(data) != size:
            raise RuntimeError("truncated git cat-file --batch object")
        pos += size + 1
        if obj_type == "blob":
            blobs[obj_sha] = data
    return blobs


def _is_media_file(rel_path: str, data: bytes) -> bool:
    if rel_path.lower().endswith(_MEDIA_EXTENSIONS):
        return True
    if data.startswith(_MEDIA_PREFIXES):
        return True
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return True
    if len(data) >= 12:
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            return True
        if data[:4] == b"FORM" and data[8:12] in (b"AIFF", b"AIFC"):
            return True
    return False


def _check_identity(repo: Path, denylist: Sequence[str] = ()) -> set[tuple[str, int, str]]:
    findings: set[tuple[str, int, str]] = set()

    cfg = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "user.email"],
        capture_output=True,
        text=True,
        check=False,
    )
    if cfg.returncode == 0:
        configured = cfg.stdout.strip().lower()
        if configured and configured != _WORKSHOP_EMAIL:
            findings.add(("config", 0, "identity"))

    log_result = subprocess.run(
        ["git", "-C", str(repo), "log", "--all", "-z", "--format=%H%x00%ae%x00%ce%x00%B"],
        capture_output=True,
        check=False,
    )
    if log_result.returncode == 0 and log_result.stdout:
        fields = log_result.stdout.split(b"\x00")
        if fields and fields[-1] == b"":
            fields.pop()
        if len(fields) % 4 != 0:
            raise RuntimeError("unexpected git log output format")
        for idx in range(0, len(fields), 4):
            commit_hash = fields[idx].decode("utf-8", errors="replace")
            author_email = fields[idx + 1].decode("utf-8", errors="replace")
            committer_email = fields[idx + 2].decode("utf-8", errors="replace")
            message = fields[idx + 3].decode("utf-8", errors="replace")
            label = f"commit {commit_hash}"
            if (
                author_email.strip().lower() != _WORKSHOP_EMAIL
                or committer_email.strip().lower() != _WORKSHOP_EMAIL
            ):
                findings.add((label, 0, "identity"))
            for line_no, rule in scan_text(message, denylist):
                findings.add((label, line_no, rule))

    return findings


def _scan_git_entry(
    location: str,
    rel_path: str,
    mode: str,
    data: bytes,
    denylist: Sequence[str],
    normalized_deny: tuple[str, ...],
    findings: set[tuple[str, int, str]],
) -> None:
    if normalized_deny:
        path_cf = rel_path.casefold()
        if any(entry in path_cf for entry in normalized_deny):
            findings.add((location, 0, "denylist"))

    if mode == "120000":
        target_text = data.decode("utf-8", errors="replace")
        for _, rule in scan_text(target_text, denylist):
            findings.add((location, 0, rule))
        return

    if _is_media_file(rel_path, data):
        findings.add((location, 0, "media-file"))
    if len(data) > _MAX_FILE_BYTES:
        findings.add((location, 0, "large-file"))

    text = data.decode("utf-8", errors="replace")
    for line_no, rule in scan_text(text, denylist):
        findings.add((location, line_no, rule))


def check_repo(repo: Path, denylist: Sequence[str] = ()) -> tuple[int, set[tuple[str, int, str]]]:
    """Scan ``HEAD``, index, commit history, and repository identity in ``repo``."""
    index_entries = _list_index_entries(repo)
    has_head = _has_head(repo)
    head_entries = _list_head_entries(repo) if has_head else []
    normalized_deny = _normalize_denylist(denylist)
    findings: set[tuple[str, int, str]] = set()

    current_entries = head_entries + index_entries
    examined = len({rel_path for rel_path, _, _ in current_entries})
    current_path_shas = {(rel_path, sha) for rel_path, _, sha in current_entries}
    seen_blobs: set[str] = {sha for _, _, sha in current_entries}

    current_blobs = _read_blobs(repo, [sha for _, _, sha in current_entries])
    for rel_path, mode, sha in dict.fromkeys(current_entries):
        data = current_blobs.get(sha)
        if data is None:
            continue
        _scan_git_entry(rel_path, rel_path, mode, data, denylist, normalized_deny, findings)

    history_entries = _list_history_entries(repo, has_head)
    pending_history: list[tuple[str, str, str, str]] = []
    for commit_hash, rel_path, mode, sha in history_entries:
        if (rel_path, sha) in current_path_shas:
            continue
        location = f"commit {commit_hash}:{rel_path}"
        if sha in seen_blobs:
            if normalized_deny:
                path_cf = rel_path.casefold()
                if any(entry in path_cf for entry in normalized_deny):
                    findings.add((location, 0, "denylist"))
            if mode != "120000" and rel_path.lower().endswith(_MEDIA_EXTENSIONS):
                findings.add((location, 0, "media-file"))
            continue
        seen_blobs.add(sha)
        pending_history.append((commit_hash, rel_path, mode, sha))

    history_blobs = _read_blobs(repo, [sha for _, _, _, sha in pending_history])
    for commit_hash, rel_path, mode, sha in pending_history:
        data = history_blobs.get(sha)
        if data is None:
            continue
        location = f"commit {commit_hash}:{rel_path}"
        _scan_git_entry(location, rel_path, mode, data, denylist, normalized_deny, findings)

    findings.update(_check_identity(repo, denylist))
    return examined, findings


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for the leak check."""
    parser = argparse.ArgumentParser(description="Scan a Git repository for leaked information.")
    parser.add_argument("--repo", default=".", help="Path to the Git repository (default: .).")
    args = parser.parse_args(argv)

    try:
        denylist = _load_denylist()
        examined, findings = check_repo(Path(args.repo), denylist)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if findings:
        for path_label, line_no, rule in sorted(findings):
            print(f"{path_label}:{line_no}: {rule}", file=sys.stderr)
        return 1

    print(f"leak check: OK ({examined} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
