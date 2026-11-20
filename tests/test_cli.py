import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from conftest import REPO, run_sal


def test_a1_version() -> None:
    result = run_sal("--version")

    assert result.returncode == 0
    assert result.stdout == "sal 0.0.1\n"
    assert result.stderr == ""


def test_a2_version_consistency() -> None:
    import sal

    with (REPO / "pyproject.toml").open("rb") as config:
        project_version = tomllib.load(config)["project"]["version"]
    result = run_sal("--version")

    assert sal.__version__ == project_version
    assert result.stdout.split()[1:] == [project_version]


def test_a3_doctor_json_shape() -> None:
    result = run_sal("doctor", "--json")

    assert result.returncode == 0
    assert result.stdout.endswith("\n")
    assert len(result.stdout.splitlines()) == 1
    versions = json.loads(result.stdout)
    assert isinstance(versions, dict)
    assert versions.keys() == {"ffmpeg", "ffprobe", "python", "sal"}


def _tool_version(tool: str) -> str:
    output = subprocess.check_output([tool, "-version"], text=True, timeout=60)
    return output.splitlines()[0].split()[2]


def test_a4_doctor_json_versions() -> None:
    import sal

    result = run_sal("doctor", "--json")
    assert result.returncode == 0
    versions = json.loads(result.stdout)
    python_version = subprocess.check_output(
        [sys.executable, "-c", "import platform; print(platform.python_version())"],
        text=True,
        timeout=60,
    ).strip()

    assert versions["sal"] == sal.__version__
    assert versions["python"] == python_version
    assert versions["ffmpeg"] == _tool_version("ffmpeg")
    assert versions["ffprobe"] == _tool_version("ffprobe")


def test_a5_doctor_missing_ffmpeg(tmp_path: Path) -> None:
    result = run_sal("doctor", "--json", env={**os.environ, "PATH": str(tmp_path)})

    assert result.returncode != 0
    assert result.stdout == ""
    assert re.search(r"\bffmpeg\b", result.stderr)


def test_a6_doctor_text_version() -> None:
    result = run_sal("doctor")

    assert result.returncode == 0
    assert _tool_version("ffmpeg") in result.stdout


@pytest.mark.parametrize(
    "command",
    [
        "view render",
        "round new",
        "round build",
        "round serve",
        "comment add",
        "comment answer",
        "inbox",
    ],
)
def test_a7_subcommand_help(command: str) -> None:
    result = run_sal(*command.split(), "--help")

    assert result.returncode == 0
    assert result.stdout != ""


def test_a8_unknown_command() -> None:
    result = run_sal("nosuchcommand")

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr != ""


def test_a9_main_version(capsys: pytest.CaptureFixture[str]) -> None:
    from sal.cli import main

    try:
        status = main(["--version"])
    except SystemExit as exc:
        status = exc.code

    assert status == 0
    assert capsys.readouterr().out == "sal 0.0.1\n"
