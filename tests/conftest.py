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


def render(
    view: str, input_path: Path, out_dir: Path, span: str | None = None
) -> subprocess.CompletedProcess[str]:
    args = ["view", "render", view, "--input", str(input_path)]
    if span is not None:
        if span.startswith("-"):
            args.append(f"--span={span}")
        else:
            args.extend(["--span", span])
    return run_sal(*args, "--out", str(out_dir))


def load_view(out_dir: Path) -> tuple[str, dict]:
    import json

    def reject_constant(value: str) -> None:
        raise ValueError(f"Non-finite JSON constant: {value}")

    text = (out_dir / "view.json").read_bytes().decode("utf-8")
    return text, json.loads(text, parse_constant=reject_constant)


def _wav_fixture(name: str, segments: list[tuple[float, float, float]]):
    import pytest
    from media import make_wav

    @pytest.fixture(scope="module", name=name)
    def generate(tmp_path_factory: pytest.TempPathFactory) -> Path:
        return make_wav(tmp_path_factory.mktemp(name) / f"{name}.wav", segments)

    return generate


step_wav = _wav_fixture("step_wav", [(5.0, 1000, 0.1), (5.0, 1000, 0.2)])
burst_wav = _wav_fixture("burst_wav", [(3.0, 0, 0), (0.010, 1000, 0.5), (6.990, 0, 0)])
silent_wav = _wav_fixture("silent_wav", [(10.0, 0, 0)])


RED = (200, 40, 40)
GREEN = (40, 200, 40)
BLUE = (40, 40, 200)
YELLOW = (200, 200, 40)


def _video_fixtures():
    import pytest
    from media import make_video, make_wav

    @pytest.fixture(scope="module")
    def av_mp4(tmp_path_factory: pytest.TempPathFactory) -> Path:
        directory = tmp_path_factory.mktemp("av")
        audio = make_wav(directory / "av.wav", [(6.0, 1000, 0.1), (6.0, 1000, 0.2)])
        return make_video(
            directory / "av.mp4",
            [
                ("move", 75, RED),
                ("move", 50, GREEN),
                ("black", 25),
                ("move", 50, BLUE),
                ("freeze", 25),
                ("move", 25, BLUE),
                ("move", 50, YELLOW),
            ],
            audio=audio,
        )

    @pytest.fixture(scope="module")
    def ntsc_mp4(tmp_path_factory: pytest.TempPathFactory) -> Path:
        directory = tmp_path_factory.mktemp("ntsc")
        audio = make_wav(directory / "ntsc.wav", [(4.004, 1000, 0.1)])
        return make_video(
            directory / "ntsc.mp4",
            [("move", 48, RED), ("move", 48, GREEN)],
            fps="24000/1001",
            audio=audio,
        )

    @pytest.fixture(scope="module")
    def av_render(
        tmp_path_factory: pytest.TempPathFactory, av_mp4: Path
    ) -> tuple[Path, subprocess.CompletedProcess[str]]:
        out_dir = tmp_path_factory.mktemp("av-render") / "out"
        return out_dir, render("av_transition", av_mp4, out_dir)

    @pytest.fixture(scope="module")
    def av_view(av_render: tuple[Path, subprocess.CompletedProcess[str]]) -> tuple[Path, dict, str]:
        out_dir, process = av_render
        assert process.returncode == 0, process.stderr
        text, view = load_view(out_dir)
        return out_dir, view, text

    return av_mp4, ntsc_mp4, av_render, av_view


av_mp4, ntsc_mp4, av_render, av_view = _video_fixtures()
