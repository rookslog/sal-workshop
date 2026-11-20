import subprocess
import sys
from pathlib import Path

from conftest import REPO

CONFIG = str(REPO / "pyproject.toml")
LINT = ("ruff", "check", "--config", CONFIG)
FORMAT = ("ruff", "format", "--check", "--config", CONFIG)
TYPE_CHECK = ("mypy", "--config-file", CONFIG)


def run_check(command: tuple[str, ...], file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", *command, str(file)],
        cwd=file.parent,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_c1_unused_import(tmp_path: Path) -> None:
    file = tmp_path / "sample.py"
    file.write_text("import os\n\nx = 1\n", encoding="utf-8")

    assert run_check(LINT, file).returncode != 0


def test_c2_bad_format(tmp_path: Path) -> None:
    file = tmp_path / "sample.py"
    file.write_text("x = {  'a':1 }\n", encoding="utf-8")

    assert run_check(FORMAT, file).returncode != 0


def test_c3_missing_annotations(tmp_path: Path) -> None:
    file = tmp_path / "sample.py"
    file.write_text("def f(x):\n    return x\n", encoding="utf-8")

    assert run_check(TYPE_CHECK, file).returncode != 0


def test_c4_good_file(tmp_path: Path) -> None:
    file = tmp_path / "sample.py"
    file.write_text("def f(x: int) -> int:\n    return x + 1\n", encoding="utf-8")

    for command in (LINT, FORMAT, TYPE_CHECK):
        result = run_check(command, file)
        assert result.returncode == 0, result.stdout + result.stderr
