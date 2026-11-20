from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import ROUND_LABELS, open_page, read_json, view_x

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

pytestmark = pytest.mark.browser

type OpenedPage = tuple[Page, list[str], list[str], list[str]]

TIMES = "() => Array.from(document.querySelectorAll('video'), video => video.currentTime)"
STATE = """() => Array.from(document.querySelectorAll('video'), video => ({
    paused: video.paused, muted: video.muted, volume: video.volume
}))"""
COMMENT_TEXT = "a 1 l 2 L "


@contextmanager
def _fresh_page(browser_name: str, round_dir: Path) -> Iterator[OpenedPage]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        opened = open_page(playwright, browser_name, round_dir)
        browser = opened[0].context.browser
        assert browser is not None
        try:
            yield opened
        finally:
            browser.close()


@pytest.fixture
def page(browser_name: str, plain_round: Path) -> Iterator[OpenedPage]:
    with _fresh_page(browser_name, plain_round) as opened:
        yield opened


def times(page: Page) -> list[float]:
    return page.evaluate(TIMES)


def state(page: Page) -> list[dict]:
    return page.evaluate(STATE)


def _manifest(round_dir: Path) -> dict:
    return read_json(round_dir / "build" / "page.json")


def _row(page: Page, index: int) -> Locator:
    return page.locator(f'.take[data-index="{index}"]')


def _active(page: Page, index: int) -> None:
    assert page.locator(".take.active").evaluate_all(
        "(rows) => rows.map(row => row.dataset.index)"
    ) == [str(index)]


def _no_errors(opened: OpenedPage) -> None:
    _, console_errors, page_errors, _ = opened
    assert console_errors == []
    assert page_errors == []


def _view_middle(page: Page, index: int) -> float:
    image = _row(page, index).locator("img.view-image")
    image.scroll_into_view_if_needed()
    box = image.bounding_box()
    assert box is not None
    return box["y"] + box["height"] / 2


def _click_view(page: Page, index: int, t: float) -> None:
    y = _view_middle(page, index)
    page.mouse.click(view_x(page, index, t), y)


def _drag_view(page: Page, index: int, start: float, end: float) -> None:
    y = _view_middle(page, index)
    start_x, end_x = view_x(page, index, start), view_x(page, index, end)
    page.mouse.move(start_x, y)
    page.mouse.down()
    page.mouse.move(end_x, y, steps=8)
    page.mouse.up()


def _comment_attributes(page: Page) -> tuple[str, str, str]:
    box = page.locator("#comment-box")
    box.wait_for(state="visible")
    start = box.get_attribute("data-start")
    end = box.get_attribute("data-end")
    sha = box.get_attribute("data-sha256")
    assert start is not None and re.fullmatch(r"[0-9]+\.[0-9]{3}", start)
    assert end is not None and re.fullmatch(r"[0-9]+\.[0-9]{3}", end)
    assert sha is not None
    return start, end, sha


def _view_click_seeks_all_and_opens_comment(page: Page, round_dir: Path) -> None:
    _click_view(page, 2, 7.0)
    start, end, sha = _comment_attributes(page)
    assert start == end
    assert float(start) == pytest.approx(7.0, abs=0.1)
    assert sha == _manifest(round_dir)["takes"][1]["sha256"]

    # Give the paused seek's playhead update a paint before measuring its position.
    page.evaluate(
        "() => new Promise(resolve => {"
        "    requestAnimationFrame(() => requestAnimationFrame(resolve));"
        "})"
    )
    current_times = times(page)
    assert current_times == pytest.approx([7.0, 7.0], abs=0.1)
    for index, current in enumerate(current_times, start=1):
        playhead = _row(page, index).locator(".playhead").bounding_box()
        assert playhead is not None
        centre = playhead["x"] + playhead["width"] / 2
        assert centre == pytest.approx(view_x(page, index, current), abs=2)


def _switch_after_paused_seek(page: Page) -> None:
    _click_view(page, 1, 7.0)
    page.keyboard.press("Escape")
    page.keyboard.press("2")
    _active(page, 2)
    assert times(page)[1] == pytest.approx(7.0, abs=0.1)
    page.keyboard.press("Space")
    page.wait_for_timeout(600)
    first, second = state(page)
    assert not second["paused"]
    assert not second["muted"]
    assert first["muted"]


def _view_drag_selects_without_seeking(page: Page, round_dir: Path) -> None:
    before = times(page)
    _drag_view(page, 1, 3.0, 5.0)
    start, end, sha = _comment_attributes(page)
    assert float(start) == pytest.approx(3.0, abs=0.1)
    assert float(end) == pytest.approx(5.0, abs=0.1)
    assert sha == _manifest(round_dir)["takes"][0]["sha256"]
    selection = _row(page, 1).locator(".selection").bounding_box()
    assert selection is not None
    assert selection["x"] == pytest.approx(view_x(page, 1, 3.0), abs=3)
    assert selection["x"] + selection["width"] == pytest.approx(view_x(page, 1, 5.0), abs=3)
    assert times(page) == pytest.approx(before, abs=0.05)


def _focus_comment(page: Page) -> Locator:
    _click_view(page, 1, 2.0)
    text = page.locator("#comment-text")
    text.click()
    return text


def test_g1_load_renders_question_rows_and_views(page: OpenedPage, plain_round: Path) -> None:
    opened, _, _, _ = page
    _no_errors(page)
    question = read_json(plain_round / "round.json")["question"]
    assert opened.locator("#question").text_content() == question
    rows = opened.locator(".take").all()
    assert len(rows) == 2
    for index, (row, take) in enumerate(
        zip(rows, _manifest(plain_round)["takes"], strict=True), start=1
    ):
        assert row.get_attribute("data-index") == str(index)
        assert row.get_attribute("data-sha256") == take["sha256"]
        assert row.locator(".take-label").text_content() == take["shown"]
        width = row.locator("img.view-image").evaluate("(image) => image.naturalWidth")
        assert width == take["view"]["width"]
    _active(opened, 1)


def test_g2_initial_video_state(page: OpenedPage) -> None:
    opened = page[0]
    durations = opened.locator("video").evaluate_all(
        "(videos) => videos.map(video => video.duration)"
    )
    assert durations == pytest.approx([12.0, 12.0], abs=0.1)
    first, second = state(opened)
    assert first["paused"] and second["paused"]
    assert not first["muted"]
    assert second["muted"]


def test_g3_space_plays_and_pauses(page: OpenedPage) -> None:
    opened = page[0]
    opened.keyboard.press("Space")
    opened.wait_for_timeout(1000)
    first, second = state(opened)
    assert not first["paused"]
    assert times(opened)[0] >= 0.3
    assert second["muted"]
    opened.keyboard.press("Space")
    opened.wait_for_function(f"() => ({STATE})().every(video => video.paused)", timeout=500)
    before = times(opened)
    opened.wait_for_timeout(400)
    assert all(
        abs(after - start) < 0.05 for start, after in zip(before, times(opened), strict=True)
    )


def test_g4_view_click_seeks_and_opens_comment(page: OpenedPage, plain_round: Path) -> None:
    _view_click_seeks_all_and_opens_comment(page[0], plain_round)


def test_g5_switch_after_paused_seek(page: OpenedPage) -> None:
    _switch_after_paused_seek(page[0])


def _switch_during_playback(page: Page, previous: int, next_take: int) -> None:
    before = times(page)[previous - 1]
    page.keyboard.press(str(next_take))
    after = times(page)[next_take - 1]
    assert -0.1 <= after - before <= 0.5
    page.wait_for_timeout(500)
    assert times(page)[next_take - 1] - after >= 0.2
    videos = state(page)
    assert not videos[next_take - 1]["paused"]
    assert videos[previous - 1]["muted"]


def test_g6_switch_during_playback(page: OpenedPage) -> None:
    opened = page[0]
    _click_view(opened, 1, 3.0)
    opened.keyboard.press("Escape")
    opened.keyboard.press("Space")
    opened.wait_for_timeout(800)
    _switch_during_playback(opened, 1, 2)
    _switch_during_playback(opened, 2, 1)


def test_g7_out_of_range_keys_do_nothing(page: OpenedPage) -> None:
    opened = page[0]
    for key in ("3", "9", "0"):
        opened.keyboard.press(key)
    _no_errors(page)
    _active(opened, 1)
    assert all(video["paused"] for video in state(opened))


def test_g8_drag_selects_without_seeking(page: OpenedPage, plain_round: Path) -> None:
    _view_drag_selects_without_seeking(page[0], plain_round)


def test_g9_reverse_drag_orders_times(page: OpenedPage) -> None:
    opened = page[0]
    _drag_view(opened, 1, 5.0, 3.0)
    start, end, _ = _comment_attributes(opened)
    assert float(start) == pytest.approx(3.0, abs=0.1)
    assert float(end) == pytest.approx(5.0, abs=0.1)


def test_g10_loop(page: OpenedPage) -> None:
    opened = page[0]
    _drag_view(opened, 1, 3.0, 4.0)
    opened.keyboard.press("Escape")
    opened.keyboard.press("L")
    opened.keyboard.press("Space")
    samples = opened.evaluate(
        "() => new Promise(resolve => { const readTimes = "
        + TIMES
        + """;
            const samples = [];
            const timer = setInterval(() => {
                samples.push(readTimes()[0]);
                if (samples.length === 38) {
                    clearInterval(timer);
                    resolve(samples);
                }
            }, 100);
        })"""
    )
    assert all(2.9 <= sample <= 4.35 for sample in samples), samples
    assert sum(right < left for left, right in pairwise(samples)) >= 2, samples
    opened.keyboard.press("L")
    opened.wait_for_function(f"() => ({TIMES})()[0] > 4.5", polling=100, timeout=2500)


def test_g11_loop_key_without_selection_does_nothing(page: OpenedPage) -> None:
    opened = page[0]
    opened.keyboard.press("L")
    opened.keyboard.press("Space")
    opened.wait_for_timeout(800)
    assert times(opened)[0] >= 0.3
    _no_errors(page)


def test_g12_typing_does_not_trigger_shortcuts(page: OpenedPage) -> None:
    opened = page[0]
    text = _focus_comment(opened)
    before = times(opened)
    text.press_sequentially(COMMENT_TEXT)
    assert text.input_value() == COMMENT_TEXT
    _active(opened, 1)
    assert all(video["paused"] for video in state(opened))
    assert times(opened) == pytest.approx(before, abs=0.05)


def test_g13_escape_keeps_unsaved_text(page: OpenedPage) -> None:
    opened = page[0]
    _focus_comment(opened).press_sequentially(COMMENT_TEXT)
    opened.keyboard.press("Escape")
    box = opened.locator("#comment-box")
    box.wait_for(state="hidden")
    _click_view(opened, 1, 2.0)
    box.wait_for(state="visible")
    assert opened.locator("#comment-text").input_value() == COMMENT_TEXT


def test_g14_level_match_toggle(page: OpenedPage, plain_round: Path) -> None:
    opened = page[0]
    takes = _manifest(plain_round)["takes"]
    gains = [take["gain_db"] for take in takes]
    for index, gain in enumerate(gains, start=1):
        text = _row(opened, index).locator(".take-gain").text_content()
        assert text is not None and f"{gain:.1f} dB" in text
    level_match = opened.locator("#level-match")
    assert level_match.is_checked()
    expected = [10 ** (gain / 20) for gain in gains]
    assert [video["volume"] for video in state(opened)] == pytest.approx(expected, abs=0.01)
    level_match.uncheck()
    assert [video["volume"] for video in state(opened)] == [1.0, 1.0]
    level_match.check()
    assert [video["volume"] for video in state(opened)] == pytest.approx(expected, abs=0.01)


def test_g15_playhead_tracks_playback(page: OpenedPage) -> None:
    opened = page[0]
    _click_view(opened, 1, 1.0)
    opened.keyboard.press("Escape")
    opened.keyboard.press("Space")
    opened.wait_for_timeout(1000)
    t, centre = _row(opened, 1).evaluate(
        """row => {
            const time = row.querySelector('video').currentTime;
            const box = row.querySelector('.playhead').getBoundingClientRect();
            return [time, box.x + box.width / 2];
        }"""
    )
    assert centre == pytest.approx(
        view_x(opened, 1, t), abs=2 + 0.15 * (view_x(opened, 1, 1.0) - view_x(opened, 1, 0.0))
    )


def test_g16_offline_comment_fields(page: OpenedPage) -> None:
    opened = page[0]
    _click_view(opened, 1, 2.0)
    opened.locator("#comment-box").wait_for(state="visible")
    status = opened.locator("#comment-status")
    assert status.get_attribute("data-state") == "offline"
    message = status.text_content()
    assert message is not None and "cannot be saved" in message
    assert opened.locator("#comment-save").is_disabled()
    kind = opened.locator("#comment-kind")
    options = kind.locator("option").evaluate_all(
        "(options) => options.map(option => option.value)"
    )
    assert len(options) == 4
    assert set(options) == {"flag", "question", "request", "verdict"}
    assert options[0] == "flag"
    assert kind.input_value() == "flag"


def test_g17_click_and_drag_only_request_local_files(page: OpenedPage, plain_round: Path) -> None:
    opened, _, _, requests = page
    _view_click_seeks_all_and_opens_comment(opened, plain_round)
    opened.keyboard.press("Escape")
    _view_drag_selects_without_seeking(opened, plain_round)
    assert all(url.startswith("file://") for url in requests), requests


def test_g18_blind_page_labels_seek_and_switch(browser_name: str, blind_round: Path) -> None:
    with _fresh_page(browser_name, blind_round) as (opened, _, _, _):
        assert opened.locator(".take-label").all_text_contents() == ["A", "B"]
        body = opened.locator("body").inner_text()
        assert all(label not in body for label in ROUND_LABELS)
        _view_click_seeks_all_and_opens_comment(opened, blind_round)
        opened.keyboard.press("Escape")
        _switch_after_paused_seek(opened)


def test_g19_playback_at_end_has_no_errors(page: OpenedPage) -> None:
    opened = page[0]
    _click_view(opened, 1, 11.6)
    opened.keyboard.press("Escape")
    opened.keyboard.press("Space")
    opened.wait_for_timeout(1500)
    _no_errors(page)
    assert opened.locator(".take.active video").evaluate("(video) => video.paused || video.ended")
    opened.keyboard.press("Space")
    opened.wait_for_timeout(500)
    _no_errors(page)
