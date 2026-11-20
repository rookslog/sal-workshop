from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import ROUND_LABELS, make_round, sized_page
from test_page import _click_view, _view_click_seeks_all_and_opens_comment, times
from test_page_save import _wait_for_paint

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.browser

SCROLL_TO_BOTTOM = "() => window.scrollTo(0, document.documentElement.scrollHeight)"


@pytest.fixture
def stage_page(browser_name: str, plain_round: Path) -> Iterator[Page]:
    with sized_page(browser_name, plain_round) as page:
        yield page


def _layout(page: Page) -> dict:
    return page.evaluate(
        """() => {
            const boxes = selector => Array.from(document.querySelectorAll(selector),
                element => element.getBoundingClientRect().toJSON());
            return {
                videos: boxes('video'),
                charts: boxes('img.view-image'),
                commentBox: boxes('#comment-box')[0],
                viewport: [window.innerWidth, window.innerHeight],
                scrollWidth: document.documentElement.scrollWidth,
                clientWidth: document.documentElement.clientWidth
            };
        }"""
    )


def _assert_inside_window(rect: dict, viewport: list[int]) -> None:
    width, height = viewport
    assert 0 <= rect["left"] <= rect["right"] <= width, rect
    assert 0 <= rect["top"] <= rect["bottom"] <= height, rect


def _assert_large_stage(page: Page) -> None:
    layout = _layout(page)
    first, second = layout["videos"]
    assert first["width"] >= 480
    _assert_inside_window(first, layout["viewport"])
    assert second["width"] <= 240
    first_chart, second_chart = layout["charts"]
    assert first_chart["width"] >= 760 and second_chart["width"] >= 760
    assert layout["scrollWidth"] <= layout["clientWidth"]


def test_b1_large_stage_and_charts(stage_page: Page) -> None:
    _assert_large_stage(stage_page)


def test_b2_switch_makes_second_video_large(stage_page: Page) -> None:
    _assert_large_stage(stage_page)
    stage_page.keyboard.press("2")
    _wait_for_paint(stage_page)

    layout = _layout(stage_page)
    first, second = layout["videos"]
    assert second["width"] >= 480
    _assert_inside_window(second, layout["viewport"])
    assert first["width"] <= 240


def test_b3_video_stays_in_sight(stage_page: Page) -> None:
    _assert_large_stage(stage_page)
    stage_page.evaluate(SCROLL_TO_BOTTOM)
    stage_page.wait_for_timeout(200)

    layout = _layout(stage_page)
    first = layout["videos"][0]
    assert first["width"] >= 480
    _assert_inside_window(first, layout["viewport"])


def test_b4_comment_and_active_video_stay_in_sight(
    browser_name: str, tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    round_dir = make_round(tmp_path, (take_low_mp4, take_high_mp4), view="av_transition")
    with sized_page(browser_name, round_dir) as page:
        page.evaluate(SCROLL_TO_BOTTOM)
        _click_view(page, 2, 5.0)
        page.locator("#comment-box").wait_for(state="visible")
        _wait_for_paint(page)

        layout = _layout(page)
        _assert_inside_window(layout["commentBox"], layout["viewport"])
        _assert_inside_window(layout["videos"][0], layout["viewport"])
        assert times(page) == pytest.approx([5.0, 5.0], abs=0.1)


def test_b5_narrow_stage_has_no_sideways_scroll(browser_name: str, plain_round: Path) -> None:
    with sized_page(browser_name, plain_round, width=900, height=700) as page:
        layout = _layout(page)
        assert layout["scrollWidth"] <= layout["clientWidth"]
        assert layout["videos"][0]["width"] >= 300
        _view_click_seeks_all_and_opens_comment(page, plain_round)


def test_b6_videos_stay_in_their_rows(stage_page: Page) -> None:
    _assert_large_stage(stage_page)
    indices = stage_page.evaluate(
        """() => Array.from(document.querySelectorAll('video'),
            video => video.closest('.take')?.dataset.index)"""
    )
    assert indices == ["1", "2"]


def test_b7_blind_round_keeps_stage_sizes_and_hides_labels(
    browser_name: str, blind_round: Path
) -> None:
    with sized_page(browser_name, blind_round) as page:
        _assert_large_stage(page)
        body = page.locator("body").inner_text()
        assert all(label not in body for label in ROUND_LABELS)
