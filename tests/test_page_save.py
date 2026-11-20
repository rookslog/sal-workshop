from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import (
    comment_files,
    comment_paths,
    comment_record,
    open_served_page,
    read_json,
    served,
)
from test_page import OpenedPage, _row, _view_middle, times

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page, Playwright

pytestmark = pytest.mark.browser

QUESTION_TEXT = "  why here?  "


@contextmanager
def _browser_page(playwright: Playwright, browser_name: str, url: str) -> Iterator[OpenedPage]:
    opened = open_served_page(playwright, browser_name, url)
    browser = opened[0].context.browser
    assert browser is not None
    try:
        yield opened
    finally:
        browser.close()


@contextmanager
def _live_page(browser_name: str, round_dir: Path) -> Iterator[tuple[OpenedPage, str, Path]]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright, served(round_dir) as (url, root):
        with _browser_page(playwright, browser_name, url) as opened:
            yield opened, url, root


@pytest.fixture
def live_page(browser_name: str, comment_round: Path) -> Iterator[tuple[OpenedPage, str, Path]]:
    with _live_page(browser_name, comment_round) as opened:
        yield opened


def _page_takes(round_dir: Path) -> list[dict]:
    return read_json(round_dir / "build" / "page.json")["takes"]


def _view_x(page: Page, round_dir: Path, index: int, t: float) -> float:
    from sal.views.ruler import Ruler, time_to_x

    # WP-30's view_x reads page.json via a file URL; use the build path for HTTP pages.
    view = _page_takes(round_dir)[index - 1]["view"]
    box = _row(page, index).locator("img.view-image").bounding_box()
    assert box is not None
    return box["x"] + time_to_x(t, Ruler(**view["ruler"])) * box["width"] / view["width"]


def _click_view(page: Page, round_dir: Path, index: int, t: float) -> None:
    y = _view_middle(page, index)
    page.mouse.click(_view_x(page, round_dir, index, t), y)


def _drag_question_span(page: Page, round_dir: Path) -> None:
    y = _view_middle(page, 1)
    start = _view_x(page, round_dir, 1, 3.0)
    end = _view_x(page, round_dir, 1, 5.0)
    page.mouse.move(start, y)
    page.mouse.down()
    page.mouse.move(end, y, steps=8)
    page.mouse.up()


def _mark(page: Page, index: int) -> Locator:
    return _row(page, index).locator(".comment-mark")


def _wait_for_paint(page: Page) -> None:
    page.evaluate(
        "() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))"
    )


def _save(page: Page, index: int) -> str:
    page.locator("#comment-save").click()
    page.locator('#comment-status[data-state="saved"]').wait_for(state="attached", timeout=3000)
    mark = _mark(page, index)
    assert mark.is_visible()
    comment_id = mark.get_attribute("data-id")
    assert comment_id
    assert page.locator("#comment-box").is_hidden()
    return comment_id


def _save_question(page: Page, round_dir: Path, root: Path) -> dict:
    _drag_question_span(page, round_dir)
    page.locator("#comment-kind").select_option("question")
    page.locator("#comment-text").press_sequentially(QUESTION_TEXT)
    comment_id = _save(page, 1)
    (record,) = comment_files(root)
    assert record["id"] == comment_id
    assert record["text"] == QUESTION_TEXT
    assert record["kind"] == "question"
    assert record["span"]["start"] == pytest.approx(3.0, abs=0.1)
    assert record["span"]["end"] == pytest.approx(5.0, abs=0.1)
    assert record["take"] == {
        "sha256": _page_takes(round_dir)[0]["sha256"],
        "label_shown": "zebra-first",
    }
    return record


def _assert_stretch_mark(page: Page, round_dir: Path, record: dict) -> None:
    box = _mark(page, 1).bounding_box()
    assert box is not None
    start = _view_x(page, round_dir, 1, record["span"]["start"])
    end = _view_x(page, round_dir, 1, record["span"]["end"])
    assert box["x"] == pytest.approx(start, abs=3)
    assert box["x"] + box["width"] == pytest.approx(end, abs=3)
    assert _mark(page, 2).count() == 0


def _reload_with_mark(page: Page, comment_id: str) -> None:
    page.reload()
    mark = _mark(page, 1)
    mark.wait_for(state="visible")
    assert mark.get_attribute("data-id") == comment_id


def _save_moment(page: Page, round_dir: Path, root: Path, first_id: str) -> None:
    _click_view(page, round_dir, 2, 7.0)
    assert page.locator("#comment-kind").input_value() == "flag"
    assert page.locator("#comment-text").input_value() == ""
    comment_id = _save(page, 2)

    records = {record["id"]: record for record in comment_files(root)}
    assert len(comment_paths(root)) == 2
    assert records.keys() == {first_id, comment_id}
    record = records[comment_id]
    assert record["kind"] == "flag"
    assert record["text"] == ""
    assert record["span"]["start"] == record["span"]["end"]
    assert record["span"]["start"] == pytest.approx(7.0, abs=0.1)
    assert record["take"]["sha256"] == _page_takes(round_dir)[1]["sha256"]
    box = _mark(page, 2).bounding_box()
    assert box is not None and box["width"] >= 3
    moment_x = _view_x(page, round_dir, 2, 7.0)
    assert box["x"] <= moment_x <= box["x"] + box["width"]


def test_w1_saves_question(live_page: tuple[OpenedPage, str, Path], comment_round: Path) -> None:
    opened, _, root = live_page
    _save_question(opened[0], comment_round, root)


def test_w2_mark_spans_saved_times(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, _, root = live_page
    page = opened[0]
    record = _save_question(page, comment_round, root)
    _assert_stretch_mark(page, comment_round, record)


def test_w3_mark_survives_reload(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, _, root = live_page
    page = opened[0]
    record = _save_question(page, comment_round, root)
    _reload_with_mark(page, record["id"])


def test_w4_saves_empty_flag_at_moment(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, _, root = live_page
    page = opened[0]
    record = _save_question(page, comment_round, root)
    _reload_with_mark(page, record["id"])
    _save_moment(page, comment_round, root, record["id"])


def test_w5_failed_save_keeps_text(browser_name: str, comment_round: Path) -> None:
    from contextlib import ExitStack

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright, ExitStack() as browser_lifetime:
        with served(comment_round) as (url, root):
            opened = browser_lifetime.enter_context(_browser_page(playwright, browser_name, url))
            page = opened[0]
            _drag_question_span(page, comment_round)
            page.locator("#comment-text").press_sequentially("do not lose me")

        page.locator("#comment-save").click()
        status = page.locator('#comment-status[data-state="error"]')
        status.wait_for(state="attached", timeout=5000)
        assert page.locator("#comment-box").is_visible()
        assert page.locator("#comment-text").input_value() == "do not lose me"
        assert page.locator(".comment-mark").count() == 0
        assert comment_paths(root) == []


def test_w6_blind_save_records_letter_and_hash(
    browser_name: str, blind_comment_round: Path
) -> None:
    private = read_json(blind_comment_round / "build.private.json")
    sha256 = next(take["sha256"] for take in private["takes"] if take["shown"] == "B")
    with _live_page(browser_name, blind_comment_round) as (opened, _, root):
        page = opened[0]
        labels = page.locator(".take-label").all_text_contents()
        assert "B" in labels
        index = labels.index("B") + 1
        _click_view(page, blind_comment_round, index, 7.0)
        comment_id = _save(page, index)
        (record,) = comment_files(root)
    assert record["id"] == comment_id
    assert record["take"] == {"sha256": sha256, "label_shown": "B"}


def test_w7_other_round_has_no_mark(browser_name: str, comment_round: Path) -> None:
    from sal.comments import comment_id, write_comment

    root = comment_round.parent.parent
    record = comment_record(round_id="other", sha256=_page_takes(comment_round)[0]["sha256"])
    record["id"] = comment_id(record)
    write_comment(root, record)
    with _live_page(browser_name, comment_round) as (opened, _, _):
        page = opened[0]
        page.wait_for_function(
            """() => {
                const url = new URL('/comments', location.href).href;
                return performance.getEntriesByName(url).length > 0;
            }""",
            timeout=3000,
        )
        _wait_for_paint(page)
        assert page.locator(".comment-mark").count() == 0


def test_w8_seeking_and_playback_over_http(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, _, _ = live_page
    page = opened[0]
    _click_view(page, comment_round, 1, 7.0)
    _wait_for_paint(page)
    before = times(page)
    assert before == pytest.approx([7.0, 7.0], abs=0.1)
    page.keyboard.press("Escape")
    page.keyboard.press("Space")
    page.wait_for_timeout(800)
    assert times(page)[0] - before[0] >= 0.3


def test_w9_reopened_box_has_empty_text(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, _, root = live_page
    page = opened[0]
    _save_question(page, comment_round, root)
    _click_view(page, comment_round, 1, 9.0)
    assert page.locator("#comment-box").is_visible()
    assert page.locator("#comment-text").input_value() == ""


def test_w10_save_and_reload_use_only_served_origin(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, url, root = live_page
    page, console_errors, page_errors, requests = opened
    record = _save_question(page, comment_round, root)
    _assert_stretch_mark(page, comment_round, record)
    _reload_with_mark(page, record["id"])
    _save_moment(page, comment_round, root, record["id"])
    assert console_errors == []
    assert page_errors == []
    assert requests and all(request.startswith(url) for request in requests), requests
