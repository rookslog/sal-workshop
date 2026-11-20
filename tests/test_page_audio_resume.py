from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

import pytest
from conftest import sample, served
from test_page import OpenedPage, _active, _no_errors
from test_page_audio_only import audio_file_page as audio_file_page
from test_page_heard import _assert_only_take_heard, _file_url, _tapped_page
from test_page_heard import tone_round as tone_round
from test_page_save import _click_view

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.browser

ENGINES = ("chromium", "webkit") if sys.platform == "darwin" else ("chromium",)


@pytest.fixture(
    params=[
        pytest.param((engine, mode), id=f"{engine}-{mode}")
        for engine in ENGINES
        for mode in ("file", "served")
    ]
)
def resume_page(
    request: pytest.FixtureRequest, tone_round: Path
) -> Iterator[tuple[OpenedPage, str]]:
    engine, mode = request.param
    if mode == "served":
        with served(tone_round) as (url, _), _tapped_page(engine, url) as opened:
            yield opened, engine
    else:
        with _tapped_page(engine, _file_url(tone_round)) as opened:
            yield opened, engine


def _sample_with_ended(page: Page, count: int) -> list[list[list[float | bool]]]:
    # The shared sampler does not include ended; record it in the same browser tick.
    return page.evaluate(
        """async count => {
            const readings = [];
            for (let index = 0; index < count; index++) {
                if (index > 0) await new Promise(resolve => setTimeout(resolve, 250));
                readings.push(Array.from(document.querySelectorAll('video'), video => [
                    video.currentTime, video.paused, video.ended
                ]));
            }
            return readings;
        }""",
        count,
    )


def _assert_alive_and_together(
    page: Page, engine: str, active_take: int, least_time: float = 0.0
) -> None:
    deadline = monotonic() + 8
    take_count = len(sample(page, 250, 1)[0])
    active = active_take - 1
    webkit = engine == "webkit"
    if not webkit:
        page.wait_for_timeout(2000)
    count, advance, tolerance = (5, 1.0, 0.3) if webkit else (6, 1.25, 0.4)

    readings = []
    while not webkit or monotonic() < deadline:
        readings = _sample_with_ended(page, count)
        if webkit and monotonic() > deadline:
            break
        if all(
            len(videos) == take_count
            and all(
                not paused
                and not ended
                and current_time >= least_time
                and abs(current_time - videos[active][0]) <= 0.08
                for current_time, paused, ended in videos
            )
            for videos in readings
        ):
            elapsed = readings[-1][active][0] - readings[0][active][0]
            if elapsed == pytest.approx(advance, abs=tolerance):
                return
        if not webkit:
            break
    raise AssertionError(f"{engine}: takes did not play together; last readings: {readings!r}")


def _pause_and_resume(page: Page, play_ms: int = 3000) -> None:
    page.keyboard.press("Space")
    page.wait_for_timeout(play_ms)
    page.keyboard.press("Space")
    page.wait_for_timeout(1000)
    page.keyboard.press("Space")


def _seek_while_paused_and_play(page: Page, round_dir: Path) -> None:
    _click_view(page, round_dir, 1, 8.0)
    page.keyboard.press("Escape")
    page.keyboard.press("Space")


def test_r1_resume_keeps_both_takes_playing(resume_page: tuple[OpenedPage, str]) -> None:
    opened, engine = resume_page
    page = opened[0]
    _pause_and_resume(page)

    _assert_alive_and_together(page, engine, 1)
    _no_errors(opened)


def test_r2_switched_take_is_heard_after_resume(resume_page: tuple[OpenedPage, str]) -> None:
    opened, _ = resume_page
    page = opened[0]
    _pause_and_resume(page)
    page.keyboard.press("2")
    page.wait_for_timeout(2000)

    _assert_only_take_heard(page, 2)


def test_r3_play_after_paused_seek_keeps_takes_together(
    resume_page: tuple[OpenedPage, str], tone_round: Path
) -> None:
    opened, engine = resume_page
    page = opened[0]
    _seek_while_paused_and_play(page, tone_round)

    _assert_alive_and_together(page, engine, 1, least_time=8.0)
    _no_errors(opened)


def test_r4_switched_take_is_heard_after_paused_seek(
    resume_page: tuple[OpenedPage, str], tone_round: Path
) -> None:
    opened, _ = resume_page
    page = opened[0]
    _seek_while_paused_and_play(page, tone_round)
    page.keyboard.press("2")
    page.wait_for_timeout(2000)

    _assert_only_take_heard(page, 2)


def test_r5_switch_while_paused_resumes_with_take_two(resume_page: tuple[OpenedPage, str]) -> None:
    opened, engine = resume_page
    page = opened[0]
    page.keyboard.press("Space")
    page.wait_for_timeout(3000)
    page.keyboard.press("Space")
    page.keyboard.press("2")
    page.wait_for_timeout(500)
    page.keyboard.press("Space")

    _assert_alive_and_together(page, engine, 2)
    _active(page, 2)
    page.wait_for_timeout(2000)
    _assert_only_take_heard(page, 2)


@pytest.mark.parametrize("browser_name", ENGINES)
def test_r6_all_four_audio_takes_resume(audio_file_page: OpenedPage, browser_name: str) -> None:
    page = audio_file_page[0]
    _pause_and_resume(page, play_ms=2000)

    assert len(sample(page, 250, 1)[0]) == 4
    _assert_alive_and_together(page, browser_name, 1)


def test_r7_second_resume_keeps_both_takes_playing(resume_page: tuple[OpenedPage, str]) -> None:
    opened, engine = resume_page
    page = opened[0]
    _pause_and_resume(page)
    page.wait_for_timeout(2000)
    page.keyboard.press("Space")
    page.wait_for_timeout(1000)
    page.keyboard.press("Space")

    _assert_alive_and_together(page, engine, 1)
