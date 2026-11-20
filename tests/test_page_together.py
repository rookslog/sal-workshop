from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING

import pytest
from conftest import sample
from test_page import _active, _click_view, _row

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.browser


def test_a1_muted_take_follows_active_clock(long_page: Page) -> None:
    page = long_page
    page.keyboard.press("Space")
    page.wait_for_timeout(2000)
    samples = sample(page, 250, 24)

    for first, second in samples:
        t1, rate1, paused1, muted1 = first
        t2, rate2, paused2, muted2 = second
        assert not paused1 and not paused2
        assert not muted1 and muted2
        assert abs(t1 - t2) <= 0.08
        assert rate1 == 1
        assert 0.5 <= rate2 <= 2.0

    advance = samples[-1][0][0] - samples[0][0][0]
    assert advance == pytest.approx(6.0, abs=0.6)


def _switch_to_take_two(page: Page) -> None:
    page.keyboard.press("Space")
    page.wait_for_timeout(3000)
    _row(page, 2).locator("video").evaluate(
        """video => {
            window.wp30aSeekingEvents = 0;
            video.addEventListener('seeking', () => { window.wp30aSeekingEvents++; });
        }"""
    )
    page.keyboard.press("2")
    page.wait_for_timeout(300)

    samples = sample(page, 250, 12)
    for index, (first, second) in enumerate(samples, start=1):
        t1, _, _, muted1 = first
        t2, rate2, paused2, muted2 = second
        assert not paused2 and not muted2
        assert rate2 == 1
        assert muted1
        if index >= 6:
            assert abs(t1 - t2) <= 0.08

    second_times = [second[0] for _, second in samples]
    assert all(before <= after for before, after in pairwise(second_times)), second_times
    assert page.evaluate("() => window.wp30aSeekingEvents") == 0


def test_a2_switch_does_not_interrupt(long_page: Page) -> None:
    _switch_to_take_two(long_page)


def test_a3_switch_back_keeps_takes_together(long_page: Page) -> None:
    page = long_page
    _switch_to_take_two(page)
    page.keyboard.press("1")
    page.wait_for_timeout(1500)

    _active(page, 1)
    for first, second in sample(page, 250, 8):
        t1, rate1, _, muted1 = first
        assert not muted1
        assert rate1 == 1
        assert abs(t1 - second[0]) <= 0.08


def test_a4_takes_follow_playing_view_seek(long_page: Page, browser_name: str) -> None:
    page = long_page
    page.keyboard.press("Space")
    page.wait_for_timeout(1000)

    if browser_name == "webkit":
        from time import monotonic

        deadline = monotonic() + 8
        _click_view(page, 1, 20.0)
        page.keyboard.press("Escape")
        readings = []
        while monotonic() < deadline:
            readings = sample(page, 250, 5)
            if monotonic() > deadline:
                break
            if not all(
                len(videos) == 2
                and all(
                    current_time >= 20.0 and not paused and abs(current_time - videos[0][0]) <= 0.08
                    for current_time, _, paused, _ in videos
                )
                for videos in readings
            ):
                continue
            advance = readings[-1][0][0] - readings[0][0][0]
            if advance == pytest.approx(1.0, abs=0.3):
                return
        raise AssertionError(f"Takes did not settle within 8 s after the view click: {readings!r}")

    _click_view(page, 1, 20.0)
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)

    for first, second in sample(page, 250, 8):
        t1, _, paused1, _ = first
        t2, _, paused2, _ = second
        assert t1 >= 20.0 and t2 >= 20.0
        assert not paused1 and not paused2
        assert abs(t1 - t2) <= 0.08


def test_a5_takes_pause_together(long_page: Page) -> None:
    page = long_page
    page.keyboard.press("Space")
    page.wait_for_timeout(2000)
    page.keyboard.press("Space")
    page.wait_for_timeout(500)

    before, after = sample(page, 400, 2)
    first, second = before
    t1, _, paused1, _ = first
    t2, _, paused2, _ = second
    assert paused1 and paused2
    assert abs(t1 - t2) <= 0.05
    assert [video[0] for video in after] == pytest.approx([t1, t2], abs=0.02)


def test_a6_takes_follow_arrow_seek(long_page: Page, browser_name: str) -> None:
    page = long_page
    page.keyboard.press("Space")
    page.wait_for_timeout(2000)

    if browser_name == "webkit":
        from time import monotonic

        deadline = monotonic() + 8
        page.keyboard.press("ArrowRight")
        readings = []
        while monotonic() < deadline:
            readings = sample(page, 250, 5)
            if monotonic() > deadline:
                break
            if not all(
                len(videos) == 2
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
        raise AssertionError(f"Takes did not settle within 8 s after ArrowRight: {readings!r}")

    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(1500)

    for first, second in sample(page, 250, 6):
        t1, _, paused1, _ = first
        t2, _, paused2, _ = second
        assert abs(t1 - t2) <= 0.08
        assert not paused1 and not paused2
