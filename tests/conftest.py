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


ROUND_LABELS = ("zebra-first", "quokka-second")


def _take_fixture(name: str, amplitude: float):
    import pytest
    from media import make_video, make_wav

    @pytest.fixture(scope="session", name=name)
    def generate(tmp_path_factory: pytest.TempPathFactory) -> Path:
        directory = tmp_path_factory.mktemp(name)
        audio = make_wav(directory / f"{name}.wav", [(12.0, 1000, amplitude)])
        return make_video(
            directory / f"{name}.mp4",
            [("move", 100, RED), ("move", 100, GREEN), ("move", 100, BLUE)],
            fps="25",
            audio=audio,
        )

    return generate


take_low_mp4 = _take_fixture("take_low_mp4", 0.1)
take_high_mp4 = _take_fixture("take_high_mp4", 0.2)


def run_round_new(
    root: Path,
    takes: list[str],
    *,
    round_id: str = "r1",
    control: int = 1,
    view: str = "audio_basic",
    blind: bool = False,
    project: str | None = None,
) -> subprocess.CompletedProcess[str]:
    options = ["--question", "Which one?", "--control", str(control), "--view", view]
    for take in takes:
        options.extend(["--take", take])
    if blind:
        options.append("--blind")
    if project is not None:
        options.extend(["--project", project])
    # Options must precede the end-of-options marker for the dash-prefixed ID case.
    if round_id.startswith("-"):
        return run_sal("round", "new", *options, "--", round_id, cwd=root)
    return run_sal("round", "new", round_id, *options, cwd=root)


def run_round_build(
    root: Path, round_id: str = "r1", seed: int | None = None
) -> subprocess.CompletedProcess[str]:
    args = ["round", "build", f"rounds/{round_id}"]
    if seed is not None:
        args.extend(["--seed", str(seed)])
    return run_sal(*args, cwd=root)


def _create_round(
    root: Path,
    takes: list[Path] | tuple[Path, ...],
    *,
    blind: bool,
    view: str,
    round_id: str,
    control: int,
    labels: tuple[str, ...],
) -> Path:
    take_args = [f"{path}::{label}" for path, label in zip(takes, labels, strict=True)]
    result = run_round_new(
        root, take_args, round_id=round_id, control=control, view=view, blind=blind
    )
    assert result.returncode == 0, result.stderr
    return root / "rounds" / round_id


def make_round(
    root: Path,
    takes: list[Path] | tuple[Path, ...],
    *,
    blind: bool = False,
    view: str = "audio_basic",
    round_id: str = "r1",
    control: int = 1,
    seed: int | None = None,
    labels: tuple[str, ...] = ROUND_LABELS,
) -> Path:
    round_dir = _create_round(
        root, takes, blind=blind, view=view, round_id=round_id, control=control, labels=labels
    )
    result = run_round_build(root, round_id, seed)
    assert result.returncode == 0, result.stderr
    return round_dir


def _round_fixtures():
    import pytest

    @pytest.fixture(scope="module")
    def plain_round_build(
        tmp_path_factory: pytest.TempPathFactory, take_low_mp4: Path, take_high_mp4: Path
    ) -> tuple[Path, subprocess.CompletedProcess[str]]:
        root = tmp_path_factory.mktemp("plain-round")
        round_dir = _create_round(
            root,
            (take_low_mp4, take_high_mp4),
            blind=False,
            view="audio_basic",
            round_id="r1",
            control=1,
            labels=ROUND_LABELS,
        )
        result = run_round_build(root)
        assert result.returncode == 0, result.stderr
        return round_dir, result

    @pytest.fixture(scope="module")
    def plain_round(plain_round_build: tuple[Path, subprocess.CompletedProcess[str]]) -> Path:
        return plain_round_build[0]

    @pytest.fixture(scope="module")
    def blind_round(
        tmp_path_factory: pytest.TempPathFactory, take_low_mp4: Path, take_high_mp4: Path
    ) -> Path:
        return make_round(
            tmp_path_factory.mktemp("blind-round"),
            (take_low_mp4, take_high_mp4),
            blind=True,
            seed=3,
        )

    return plain_round_build, plain_round, blind_round


plain_round_build, plain_round, blind_round = _round_fixtures()


def _browser_fixture():
    import pytest

    names = ["chromium"]
    if sys.platform == "darwin":
        names.append("webkit")

    @pytest.fixture(params=names)
    def browser_name(request: pytest.FixtureRequest) -> str:
        return request.param

    return browser_name


browser_name = _browser_fixture()


def read_json(path: Path) -> dict:
    import json

    return json.loads(path.read_bytes())


def assert_fixed_json(path: Path) -> dict:
    import json

    data = read_json(path)
    expected = (
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    )
    assert path.read_bytes() == expected.encode("utf-8"), path
    return data


def assert_schema(name: str, data: dict) -> None:
    import json
    from importlib.resources import files

    from jsonschema import Draft202012Validator

    schema = files("sal.schemas").joinpath(f"{name}.schema.json").read_text("utf-8")
    Draft202012Validator(json.loads(schema)).validate(data)


def _browser_helpers():
    import re
    from typing import TYPE_CHECKING
    from urllib.parse import unquote, urlsplit

    if TYPE_CHECKING:
        from playwright.sync_api import ConsoleMessage, Page, Playwright, Route, WebSocketRoute

    def block_request(route: "Route") -> None:
        route.abort()

    def open_page(
        playwright: "Playwright", browser_name: str, round_dir: Path
    ) -> tuple["Page", list[str], list[str], list[str]]:
        options = {"args": ["--mute-audio"]} if browser_name == "chromium" else {}
        browser = getattr(playwright, browser_name).launch(headless=True, **options)
        context = browser.new_context(service_workers="block")
        console_errors: list[str] = []
        page_errors: list[str] = []
        requests: list[str] = []

        def block_socket(socket: "WebSocketRoute") -> None:
            requests.append(socket.url)
            socket.close()

        def collect_console_error(message: "ConsoleMessage") -> None:
            if message.type == "error":
                console_errors.append(message.text)

        context.route(re.compile(r"^(?!file://)"), block_request)
        context.route_web_socket("**/*", block_socket)
        page = context.new_page()
        page.on("console", collect_console_error)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto((round_dir / "build" / "index.html").as_uri())
        page.wait_for_function(
            """() => {
                const videos = Array.from(document.querySelectorAll('video'));
                return videos.length > 0 && videos.every(video => video.readyState >= 2);
            }""",
            timeout=10_000,
        )
        return page, console_errors, page_errors, requests

    def view_x(page: "Page", index: int, t: float) -> float:
        from sal.views.ruler import Ruler, time_to_x

        manifest = Path(unquote(urlsplit(page.url).path)).with_name("page.json")
        view = read_json(manifest)["takes"][index - 1]["view"]
        image = page.locator(f'.take[data-index="{index}"] img.view-image')
        box = image.bounding_box()
        assert box is not None
        return box["x"] + time_to_x(t, Ruler(**view["ruler"])) * box["width"] / view["width"]

    return open_page, view_x


open_page, view_x = _browser_helpers()
