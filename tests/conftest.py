import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKSHOP_EMAIL = "workshop@sal.invalid"


def run_sal(
    *args: str, env: Mapping[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "sal", *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _git_environment() -> dict[str, str]:
    # Do not let the caller's Git config, hooks, or identity affect the fixture repos.
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TEMPLATE_DIR="")
    return env


def git(repo: Path, *args: str, committer_email: str | None = None) -> None:
    env = _git_environment()
    if committer_email is not None:
        env["GIT_COMMITTER_EMAIL"] = committer_email
    subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, timeout=60, check=True
    )


def make_repo(path: Path, files: Mapping[str, str | bytes], email: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "sal workshop")
    git(path, "config", "user.email", WORKSHOP_EMAIL if email is None else email)
    for relative_path, content in files.items():
        destination = path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            destination.write_bytes(content)
        else:
            destination.write_text(content, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-m", "Create fixture")
    return path


def run_leak(
    repo: Path, denylist: Path | None = None, extra_env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = _git_environment()
    if extra_env is not None:
        env.update(extra_env)
    if denylist is None:
        env.pop("SAL_DENYLIST", None)
    else:
        env["SAL_DENYLIST"] = str(denylist)
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "leak_check.py"), "--repo", str(repo)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
