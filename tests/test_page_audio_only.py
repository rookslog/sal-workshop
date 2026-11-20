from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

import pytest
from conftest import sample
from test_page import OpenedPage, _active, _no_errors, state, times
from test_page_save import _live_page

if TYPE_CHECKING:
    from playwright.sync_api import ConsoleMessage

pytestmark = pytest.mark.browser


@pytest.fixture
def audio_file_page(browser_name: str, audio_round: Path) -> Iterator[OpenedPage]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        options = {"args": ["--mute-audio"]} if browser_name == "chromium" else {}
        browser = getattr(playwright, browser_name).launch(headless=True, **options)
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 900}, service_workers="block"
            )
            context.route(re.compile(r"^(?!file://)"), lambda route: route.abort())
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

            deadline = monotonic() + 5
            page.goto(
                (audio_round / "build" / "index.html").as_uri(),
                wait_until="domcontentloaded",
                timeout=5000,
            )
            remaining_ms = (deadline - monotonic()) * 1000
            assert remaining_ms > 0, "audio page load took more than 5 seconds"
            page.wait_for_function(
                """() => {
                    const videos = Array.from(document.querySelectorAll('video'));
                    return videos.length === 4 && videos.every(video => video.readyState >= 2);
                }""",
                timeout=remaining_ms,
            )
            yield page, console_errors, page_errors, requests
        finally:
            browser.close()


def _assert_audio_layout(opened: OpenedPage) -> None:
    page = opened[0]
    _no_errors(opened)
    assert page.locator(".take").count() == 4
    layout = page.evaluate(
        """() => ({
            videos: Array.from(document.querySelectorAll('video'), video =>
                [video.duration, video.getBoundingClientRect().width]),
            charts: Array.from(document.querySelectorAll('img.view-image'), chart =>
                chart.getBoundingClientRect().width),
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth
        })"""
    )
    assert len(layout["videos"]) == 4
    for duration, width in layout["videos"]:
        assert duration == pytest.approx(12.0, abs=0.15)
        assert width <= 240
    assert len(layout["charts"]) == 4
    assert all(width >= 760 for width in layout["charts"])
    assert page.locator("#play").count() == 1
    assert page.locator("#time").count() == 1
    assert layout["scrollWidth"] <= layout["clientWidth"]


def test_d1_audio_page_has_no_large_picture(audio_file_page: OpenedPage) -> None:
    _assert_audio_layout(audio_file_page)


def test_d2_all_four_audio_takes_play_together(
    audio_file_page: OpenedPage, browser_name: str
) -> None:
    _assert_audio_layout(audio_file_page)
    page = audio_file_page[0]

    if browser_name == "webkit":
        deadline = monotonic() + 8
        page.keyboard.press("Space")
        readings = []
        while monotonic() < deadline:
            readings = sample(page, 250, 5)
            if monotonic() > deadline:
                break
            if not all(
                len(videos) == 4
                and all(
                    not paused and abs(current_time - videos[0][0]) <= 0.08
                    for current_time, _, paused, _ in videos
                )
                for videos in readings
            ):
                continue
            advance = readings[-1][0][0] - readings[0][0][0]
            if advance == pytest.approx(1.0, abs=0.3):
                return
        raise AssertionError(f"Takes did not settle within 8 s after Space: {readings!r}")

    page.keyboard.press("Space")
    page.wait_for_timeout(3000)
    readings = sample(page, 250, 8)

    for videos in readings:
        assert len(videos) == 4
        active_time = videos[0][0]
        for current_time, _, paused, _ in videos:
            assert not paused
            assert abs(current_time - active_time) <= 0.08
    assert readings[-1][0][0] - readings[0][0][0] == pytest.approx(1.75, abs=0.4)


def test_d3_switch_to_third_audio_take(audio_file_page: OpenedPage) -> None:
    _assert_audio_layout(audio_file_page)
    page = audio_file_page[0]
    page.keyboard.press("3")
    page.keyboard.press("Space")
    page.wait_for_timeout(1500)

    _active(page, 3)
    videos = state(page)
    assert [video["muted"] for video in videos] == [True, True, False, True]
    assert all(not video["paused"] for video in videos)


def test_d4_all_audio_takes_play_over_http(browser_name: str, audio_round: Path) -> None:
    with _live_page(browser_name, audio_round) as (opened, _, _):
        page = opened[0]
        page.keyboard.press("Space")
        page.wait_for_timeout(2500)

        _no_errors(opened)
        current_times = times(page)
        assert len(current_times) == 4
        assert all(current_time >= 1.0 for current_time in current_times), current_times
