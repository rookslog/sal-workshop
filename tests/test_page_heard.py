from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from math import log10
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import make_round, read_json, served
from test_page import OpenedPage, _no_errors

if TYPE_CHECKING:
    from playwright.sync_api import ConsoleMessage, Page

pytestmark = pytest.mark.browser

audio_taps = """
(() => {
    const routed = new WeakSet();
    const taps = new Map();
    window.__testAudioTaps = {routed, taps};
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context || !window.AudioNode) return;

    const createSource = Context.prototype.createMediaElementSource;
    Context.prototype.createMediaElementSource = function (element) {
        routed.add(element);
        return createSource.call(this, element);
    };

    const connect = AudioNode.prototype.connect;
    const disconnect = AudioNode.prototype.disconnect;
    AudioNode.prototype.connect = function (...args) {
        const result = connect.apply(this, args);
        const [target, index = 0] = args;
        if (target === this.context.destination && this.context instanceof Context) {
            if (!taps.has(this)) taps.set(this, new Map());
            const outputs = taps.get(this);
            if (!outputs.has(index)) {
                const analyser = this.context.createAnalyser();
                analyser.fftSize = 8192;
                analyser.smoothingTimeConstant = 0;
                // An unconnected analyser output measures without adding sound to the mix.
                connect.call(this, analyser, index, 0);
                outputs.set(index, analyser);
            }
        }
        return result;
    };

    AudioNode.prototype.disconnect = function (...args) {
        const result = disconnect.apply(this, args);
        const outputs = taps.get(this);
        if (!outputs) return result;
        const [target, index = 0] = args;
        if (args.length === 0) {
            outputs.clear();
        } else if (typeof target === 'number') {
            outputs.delete(target);
        } else if (target === this.context.destination) {
            for (const [output, analyser] of outputs) {
                if (args.length === 1 || output === index) {
                    disconnect.call(this, analyser, output, 0);
                    outputs.delete(output);
                }
            }
        }
        if (outputs.size === 0) taps.delete(this);
        return result;
    };
})();
"""


def output(page: Page) -> dict:
    return page.evaluate(
        """() => {
            const {routed, taps} = window.__testAudioTaps;
            const videos = Array.from(document.querySelectorAll('video'), video => ({
                time: video.currentTime,
                paused: video.paused,
                muted: video.muted,
                volume: video.volume,
                routed: routed.has(video)
            }));
            const tones = [440, 660];
            const bands = [-Infinity, -Infinity];
            let squares = 0;
            let count = 0;
            for (const outputs of taps.values()) {
                for (const analyser of outputs.values()) {
                    const waveform = new Float32Array(analyser.fftSize);
                    analyser.getFloatTimeDomainData(waveform);
                    for (const value of waveform) squares += value * value;
                    count += waveform.length;

                    const spectrum = new Float32Array(analyser.frequencyBinCount);
                    analyser.getFloatFrequencyData(spectrum);
                    const binHz = analyser.context.sampleRate / analyser.fftSize;
                    for (const [index, hz] of tones.entries()) {
                        const first = Math.max(0, Math.ceil((hz - 30) / binHz));
                        const last = Math.min(spectrum.length - 1, Math.floor((hz + 30) / binHz));
                        for (let bin = first; bin <= last; bin++) {
                            bands[index] = Math.max(bands[index], spectrum[bin]);
                        }
                    }
                }
            }
            return {
                videos,
                rms: count ? Math.sqrt(squares / count) : 0,
                bands: bands.map(level => Number.isFinite(level) ? level : null)
            };
        }"""
    )


@pytest.fixture(scope="module")
def tone_round(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tone-round")
    takes = []
    for frequency, gain_db in ((440, 0), (660, 6)):
        take = root / f"tone-{frequency}.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:sample_rate=48000:duration=20",
                "-af",
                f"volume={gain_db}dB",
                "-ac",
                "2",
                "-c:a",
                "libmp3lame",
                str(take),
            ],
            check=True,
            timeout=120,
        )
        takes.append(take)
    return make_round(root, takes, view="audio_basic")


def _file_url(round_dir: Path) -> str:
    return (round_dir / "build" / "index.html").as_uri()


@contextmanager
def _tapped_page(engine: str, url: str) -> Iterator[OpenedPage]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        options = {"args": ["--mute-audio"]} if engine == "chromium" else {}
        browser = getattr(playwright, engine).launch(headless=True, **options)
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 900}, service_workers="block"
            )
            allowed_prefix = "file://" if url.startswith("file://") else url
            context.route(
                re.compile(rf"^(?!{re.escape(allowed_prefix)})"), lambda route: route.abort()
            )
            context.route_web_socket("**/*", lambda socket: socket.close())
            page = context.new_page()
            console_errors: list[str] = []
            page_errors: list[str] = []
            requests: list[str] = []

            def collect_console_error(message: ConsoleMessage) -> None:
                if message.type == "error":
                    console_errors.append(message.text)

            page.on("console", collect_console_error)
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url))
            page.add_init_script(audio_taps)
            page.goto(url)
            page.wait_for_function(
                """() => {
                    const videos = Array.from(document.querySelectorAll('video'));
                    return videos.length === 2 && videos.every(video => video.readyState >= 2);
                }""",
                timeout=10_000,
            )
            yield page, console_errors, page_errors, requests
        finally:
            browser.close()


@pytest.fixture(
    params=[
        pytest.param((engine, mode), id=f"{engine}-{mode}")
        for engine in ("chromium", "webkit")
        for mode in ("file", "served")
    ]
)
def heard_page(request: pytest.FixtureRequest, tone_round: Path) -> Iterator[OpenedPage]:
    engine, mode = request.param
    if mode == "served":
        with served(tone_round) as (url, _), _tapped_page(engine, url) as opened:
            yield opened
    else:
        with _tapped_page(engine, _file_url(tone_round)) as opened:
            yield opened


def _start_and_switch(page: Page, *, first: int, second: int) -> None:
    if first == 2:
        page.keyboard.press("2")
    page.keyboard.press("Space")
    page.wait_for_timeout(2500)
    page.keyboard.press(str(second))
    page.wait_for_timeout(2000)


def _leads_by_30_db(loud: float | None, quiet: float | None) -> bool:
    return loud is not None and (quiet is None or loud - quiet >= 30)


def _assert_only_take_heard(page: Page, index: int) -> None:
    reading = output(page)
    active = index - 1
    inactive = 1 - active
    videos = reading["videos"]
    heard, other = videos[active], videos[inactive]
    assert not heard["muted"] and not heard["paused"], reading
    if heard["routed"]:
        assert reading["rms"] > 0.002, reading
        assert _leads_by_30_db(reading["bands"][active], reading["bands"][inactive]), reading
    if not other["muted"]:
        assert other["routed"], reading
        assert (
            _leads_by_30_db(reading["bands"][active], reading["bands"][inactive])
            or reading["rms"] < 0.0005
        ), reading

    page.wait_for_function(
        """([index, start]) => {
            const video = document.querySelectorAll('video')[index];
            return !video.paused && video.currentTime > start;
        }""",
        arg=[active, heard["time"]],
        timeout=1000,
    )


def _assert_switched_to_take_two(opened: OpenedPage) -> None:
    page = opened[0]
    _start_and_switch(page, first=1, second=2)
    _assert_only_take_heard(page, 2)
    _no_errors(opened)


def test_g1_switch_to_take_two_is_heard(heard_page: OpenedPage) -> None:
    _assert_switched_to_take_two(heard_page)


def test_g2_switch_back_to_take_one_is_heard(heard_page: OpenedPage) -> None:
    _assert_switched_to_take_two(heard_page)
    page = heard_page[0]
    page.keyboard.press("1")
    page.wait_for_timeout(2000)
    _assert_only_take_heard(page, 1)


def test_g3_take_one_is_heard_after_starting_on_two(heard_page: OpenedPage) -> None:
    page = heard_page[0]
    _start_and_switch(page, first=2, second=1)
    _assert_only_take_heard(page, 1)


def _measure_level_match(page: Page) -> tuple[dict, dict]:
    toggle = page.locator("#level-match")
    assert toggle.is_checked()
    enabled = output(page)
    toggle.uncheck()
    page.wait_for_timeout(500)
    return enabled, output(page)


def _level_change_db(enabled: dict, disabled: dict) -> float:
    assert enabled["rms"] > 0 and disabled["rms"] > 0, (enabled, disabled)
    return 20 * log10(enabled["rms"] / disabled["rms"])


def test_g4_louder_take_gets_level_match_once(heard_page: OpenedPage, tone_round: Path) -> None:
    page = heard_page[0]
    _start_and_switch(page, first=1, second=2)
    enabled, disabled = _measure_level_match(page)

    gains = [take["gain_db"] for take in read_json(tone_round / "build" / "page.json")["takes"]]
    assert [video["volume"] for video in enabled["videos"]] == pytest.approx(
        [10 ** (gain / 20) for gain in gains], abs=0.01
    )
    assert [video["volume"] for video in disabled["videos"]] == [1.0, 1.0]
    if enabled["videos"][1]["routed"]:
        assert _level_change_db(enabled, disabled) == pytest.approx(gains[1], abs=1.0)


def test_g5_quiet_take_keeps_unity_level(heard_page: OpenedPage, tone_round: Path) -> None:
    page = heard_page[0]
    _start_and_switch(page, first=2, second=1)
    enabled, disabled = _measure_level_match(page)

    assert read_json(tone_round / "build" / "page.json")["takes"][0]["gain_db"] == 0.0
    assert enabled["videos"][0]["volume"] == disabled["videos"][0]["volume"] == 1.0
    if enabled["videos"][0]["routed"]:
        assert _level_change_db(enabled, disabled) == pytest.approx(0.0, abs=1.0)


def test_g6_chromium_file_does_not_route_either_take(tone_round: Path) -> None:
    with _tapped_page("chromium", _file_url(tone_round)) as opened:
        page = opened[0]
        _start_and_switch(page, first=1, second=2)
        assert [video["routed"] for video in output(page)["videos"]] == [False, False]
