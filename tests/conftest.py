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


def _server_helpers():
    import json
    import re
    from collections.abc import Iterator
    from contextlib import contextmanager
    from select import select
    from time import monotonic

    def first_line(stream) -> bytes:
        deadline = monotonic() + 5
        pending = bytearray()
        while True:
            remaining = max(0, deadline - monotonic())
            assert select([stream], [], [], remaining)[0], "round serve did not flush its URL"
            chunk = os.read(stream.fileno(), 4096)
            assert chunk, "round serve closed stdout before announcing its URL"
            pending.extend(chunk)
            if b"\n" in pending:
                return pending.partition(b"\n")[0]
            assert monotonic() < deadline, "round serve did not print a complete first line"

    @contextmanager
    def serve_process(round_dir: Path) -> Iterator[tuple[subprocess.Popen[bytes], dict]]:
        root = round_dir.parent.parent
        with subprocess.Popen(
            [
                sys.executable,
                "-m",
                "sal",
                "round",
                "serve",
                str(round_dir.relative_to(root)),
                "--port",
                "0",
            ],
            cwd=root,
            stdout=subprocess.PIPE,
        ) as process:
            try:
                assert process.stdout is not None
                announcement = json.loads(first_line(process.stdout))
                yield process, announcement
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    @contextmanager
    def served(round_dir: Path) -> Iterator[tuple[str, Path]]:
        with serve_process(round_dir) as (_, announcement):
            assert isinstance(announcement, dict)
            url = announcement["url"]
            assert re.fullmatch(r"http://127\.0\.0\.1:[0-9]+/", url)
            yield url, round_dir.parent.parent

    return serve_process, served


serve_process, served = _server_helpers()


def http(
    method: str,
    url: str,
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    from urllib.error import HTTPError
    from urllib.parse import urlsplit
    from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    target = urlsplit(url)
    assert target.scheme == "http" and target.hostname == "127.0.0.1"
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(url, data=body, headers=dict(headers or {}), method=method)
    try:
        response = opener.open(request, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return (
            response.status,
            {key.lower(): value for key, value in response.headers.items()},
            response.read(),
        )


def comment_paths(root: Path) -> list[Path]:
    directory = root / "records" / "comments"
    if not directory.exists():
        return []
    return sorted(path for path in directory.iterdir() if path.is_file())


def comment_files(root: Path) -> list[dict]:
    return [read_json(path) for path in comment_paths(root)]


def comment_record(
    *, round_id: str = "r1", sha256: str = "1" * 64, text: str = "  keep this  "
) -> dict:
    return {
        "schema": "sal.comment/1",
        "id": "",
        "created": "2026-11-20T10:15:00Z",
        "round": round_id,
        "take": {"sha256": sha256, "label_shown": "zebra-first"},
        "span": {"start": 3.0, "end": 5.0},
        "kind": "flag",
        "text": text,
        "author": "operator",
        "channel": "page",
        "answers": None,
        "replaces": None,
    }


def _comment_round_fixtures():
    import shutil
    from collections.abc import Iterator
    from contextlib import contextmanager

    import pytest

    @pytest.fixture(scope="module")
    def built_comment_round(
        tmp_path_factory: pytest.TempPathFactory, take_low_mp4: Path, take_high_mp4: Path
    ) -> Path:
        return make_round(tmp_path_factory.mktemp("comment-round"), (take_low_mp4, take_high_mp4))

    @contextmanager
    def clean_comment_round(round_dir: Path) -> Iterator[Path]:
        records = round_dir.parent.parent / "records"
        assert not records.exists()
        try:
            yield round_dir
        finally:
            if records.exists():
                shutil.rmtree(records)

    @pytest.fixture
    def comment_round(built_comment_round: Path) -> Iterator[Path]:
        with clean_comment_round(built_comment_round) as round_dir:
            yield round_dir

    @pytest.fixture
    def blind_comment_round(blind_round: Path) -> Iterator[Path]:
        with clean_comment_round(blind_round) as round_dir:
            yield round_dir

    return built_comment_round, comment_round, blind_comment_round


built_comment_round, comment_round, blind_comment_round = _comment_round_fixtures()


def _served_page_helper():
    import re
    from typing import TYPE_CHECKING
    from urllib.parse import urlsplit

    if TYPE_CHECKING:
        from playwright.sync_api import ConsoleMessage, Page, Playwright, Route, WebSocketRoute

    def open_served_page(
        playwright: "Playwright", browser_name: str, url: str
    ) -> tuple["Page", list[str], list[str], list[str]]:
        origin = urlsplit(url)
        assert origin.scheme == "http" and origin.hostname == "127.0.0.1"
        options = {"args": ["--mute-audio"]} if browser_name == "chromium" else {}
        browser = getattr(playwright, browser_name).launch(headless=True, **options)
        console_errors: list[str] = []
        page_errors: list[str] = []
        requests: list[str] = []

        def block_request(route: "Route") -> None:
            route.abort()

        def block_socket(socket: "WebSocketRoute") -> None:
            requests.append(socket.url)
            socket.close()

        def collect_console_error(message: "ConsoleMessage") -> None:
            if message.type == "error":
                console_errors.append(message.text)

        try:
            context = browser.new_context(service_workers="block")
            origin_prefix = f"{origin.scheme}://{origin.netloc}/"
            context.route(re.compile(rf"^(?!{re.escape(origin_prefix)})"), block_request)
            context.route_web_socket("**/*", block_socket)
            page = context.new_page()
            page.on("console", collect_console_error)
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            context.on("request", lambda request: requests.append(request.url))
            page.goto(url)
            page.wait_for_function(
                """() => {
                    const videos = Array.from(document.querySelectorAll('video'));
                    return videos.length > 0 && videos.every(video => video.readyState >= 2);
                }""",
                timeout=10_000,
            )
        except BaseException:
            browser.close()
            raise
        return page, console_errors, page_errors, requests

    return open_served_page


open_served_page = _served_page_helper()


def _make_long_take(path: Path, picture: str, frequency: int, volume: str) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            picture,
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration=30",
            "-af",
            f"volume={volume}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "24",
            "-sc_threshold",
            "0",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        timeout=120,
    )
    return path


def _long_page_helpers():
    import re
    from collections.abc import Iterator
    from contextlib import contextmanager
    from typing import TYPE_CHECKING

    import pytest

    if TYPE_CHECKING:
        from playwright.sync_api import Page

    @pytest.fixture(scope="session")
    def long_round(tmp_path_factory: pytest.TempPathFactory) -> Path:
        root = tmp_path_factory.mktemp("long-round")
        first = _make_long_take(
            root / "first.mp4", "testsrc2=size=640x360:rate=24:duration=30", 440, "1"
        )
        second = _make_long_take(
            root / "second.mp4", "color=color=blue:size=640x360:rate=24:duration=30", 660, "1/3"
        )
        return make_round(root, (first, second), view="audio_basic")

    @contextmanager
    def sized_page(
        browser_name: str, round_dir: Path, width: int = 1440, height: int = 900
    ) -> Iterator["Page"]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            options = {"args": ["--mute-audio"]} if browser_name == "chromium" else {}
            browser = getattr(playwright, browser_name).launch(headless=True, **options)
            try:
                context = browser.new_context(
                    viewport={"width": width, "height": height}, service_workers="block"
                )
                context.route(re.compile(r"^(?!file://)"), lambda route: route.abort())
                context.route_web_socket("**/*", lambda socket: socket.close())
                page = context.new_page()
                page.goto((round_dir / "build" / "index.html").as_uri())
                page.wait_for_function(
                    """() => {
                        const videos = Array.from(document.querySelectorAll('video'));
                        return videos.length > 0 && videos.every(video => video.readyState >= 2);
                    }""",
                    timeout=10_000,
                )
                yield page
            finally:
                browser.close()

    @pytest.fixture
    def long_page(browser_name: str, long_round: Path) -> Iterator["Page"]:
        with sized_page(browser_name, long_round) as page:
            yield page

    def sample(page: "Page", every_ms: int, count: int) -> list[list[list[float | bool]]]:
        return page.evaluate(
            """async ({everyMs, count}) => {
                const samples = [];
                for (let index = 0; index < count; index++) {
                    if (index > 0) {
                        await new Promise(resolve => setTimeout(resolve, everyMs));
                    }
                    samples.push(Array.from(document.querySelectorAll('video'), video => [
                        video.currentTime, video.playbackRate, video.paused, video.muted
                    ]));
                }
                return samples;
            }""",
            {"everyMs": every_ms, "count": count},
        )

    return long_round, sized_page, long_page, sample


long_round, sized_page, long_page, sample = _long_page_helpers()


def make_audio_take(path: Path, pitch: int, codec: str) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={pitch}:sample_rate=48000:duration=12",
            "-ac",
            "2",
            "-c:a",
            codec,
            str(path),
        ],
        check=True,
        timeout=120,
    )
    return path


def _audio_round_fixture():
    import pytest

    @pytest.fixture(scope="module")
    def audio_round(tmp_path_factory: pytest.TempPathFactory) -> Path:
        root = tmp_path_factory.mktemp("audio-round")
        takes = [
            make_audio_take(root / f"take{extension}", pitch, codec)
            for extension, pitch, codec in (
                (".m4a", 330, "aac"),
                (".mp3", 440, "libmp3lame"),
                (".wav", 550, "pcm_s16le"),
                (".opus", 660, "libopus"),
            )
        ]
        return make_round(root, takes, view="audio_basic", labels=("one", "two", "three", "four"))

    return audio_round


audio_round = _audio_round_fixture()


def _comment_save_helper():
    import json
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from playwright.sync_api import Page

    def save_on_page(
        page: "Page", index: int, start: float, end: float, kind: str, text: str
    ) -> None:
        from sal.views.ruler import Ruler, time_to_x

        # Read the same view geometry as view_x, but from the served page's embedded manifest.
        manifest = page.locator("#page-data").text_content()
        assert manifest is not None
        view = json.loads(manifest)["takes"][index - 1]["view"]
        ruler = Ruler(**view["ruler"])
        chart = page.locator(f'.take[data-index="{index}"] img.view-image')
        chart.scroll_into_view_if_needed()
        box = chart.bounding_box()
        assert box is not None
        scale = box["width"] / view["width"]
        x_start = box["x"] + time_to_x(start, ruler) * scale
        x_end = box["x"] + time_to_x(end, ruler) * scale
        y = box["y"] + box["height"] / 2

        if start == end:
            page.mouse.click(x_start, y)
        else:
            page.mouse.move(x_start, y)
            page.mouse.down()
            page.mouse.move(x_end, y, steps=8)
            page.mouse.up()

        page.locator("#comment-kind").select_option(kind)
        page.locator("#comment-text").fill(text)
        page.locator("#comment-save").click()
        page.locator('#comment-status[data-state="saved"]').wait_for(state="attached", timeout=3000)

    return save_on_page


save_on_page = _comment_save_helper()


def _uneven_round_fixture():
    import pytest
    from media import make_video, make_wav

    @pytest.fixture(scope="module")
    def uneven_round(tmp_path_factory: pytest.TempPathFactory, take_low_mp4: Path) -> Path:
        root = tmp_path_factory.mktemp("uneven-round")
        audio = make_wav(root / "short.wav", [(5.0, 1000, 0.2)])
        short = make_video(
            root / "short.mp4",
            [("move", 50, RED), ("move", 50, GREEN), ("move", 25, BLUE)],
            fps="25",
            audio=audio,
        )
        return make_round(root, (take_low_mp4, short))

    return uneven_round


uneven_round = _uneven_round_fixture()
