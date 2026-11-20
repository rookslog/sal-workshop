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


def _list_tracked_files(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git ls-files failed: {message}")
    paths = [
        raw.decode("utf-8", errors="surrogateescape") for raw in result.stdout.split(b"\x00") if raw
    ]
    return list(dict.fromkeys(paths))


def _check_identity(repo: Path) -> set[tuple[str, int, str]]:
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
        ["git", "-C", str(repo), "log", "--all", "--format=%H%x00%ae%x00%ce"],
        capture_output=True,
        text=True,
        check=False,
    )
    if log_result.returncode == 0:
        for row in log_result.stdout.splitlines():
            if not row:
                continue
            commit_hash, author_email, committer_email = row.split("\x00")
            if (
                author_email.strip().lower() != _WORKSHOP_EMAIL
                or committer_email.strip().lower() != _WORKSHOP_EMAIL
            ):
                findings.add((f"commit {commit_hash}", 0, "identity"))

    return findings


def check_repo(repo: Path, denylist: Sequence[str] = ()) -> tuple[int, set[tuple[str, int, str]]]:
    """Scan all tracked files and repository identity in ``repo``."""
    rel_paths = _list_tracked_files(repo)
    normalized_deny = _normalize_denylist(denylist)
    findings: set[tuple[str, int, str]] = set()
    examined = 0

    for rel_path in rel_paths:
        file_path = repo / rel_path
        if not file_path.is_file():
            continue
        examined += 1

        if normalized_deny:
            path_cf = rel_path.casefold()
            if any(entry in path_cf for entry in normalized_deny):
                findings.add((rel_path, 0, "denylist"))

        text = file_path.read_bytes().decode("utf-8", errors="replace")
        for line_no, rule in scan_text(text, denylist):
            findings.add((rel_path, line_no, rule))

    findings.update(_check_identity(repo))
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
