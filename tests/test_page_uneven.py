from __future__ import annotations

from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

import pytest
from conftest import comment_files, sample, save_on_page
from test_page import OpenedPage, _active, _drag_view, _fresh_page, _no_errors
from test_page_save import _click_view as _click_served_view
from test_page_save import live_page as live_page

if TYPE_CHECKING:
    from playwright.sync_api import Route

pytestmark = pytest.mark.browser


@pytest.fixture
def uneven_page(browser_name: str, uneven_round: Path) -> Iterator[OpenedPage]:
    with _fresh_page(browser_name, uneven_round) as opened:
        yield opened


def _play_past_short_take(opened: OpenedPage) -> float:
    page = opened[0]
    page.keyboard.press("Space")
    page.wait_for_timeout(6500)

    # The ordinary sampler does not include ended, which must be read with paused and time.
    readings = page.evaluate(
        """async () => {
            const videos = Array.from(document.querySelectorAll('video'));
            const samples = [];
            for (let index = 0; index < 4; index++) {
                if (index > 0) await new Promise(resolve => setTimeout(resolve, 250));
                samples.push(videos.map(video => [video.currentTime, video.paused, video.ended]));
            }
            return samples;
        }"""
    )
    long_times = []
    short_times = []
    for long_take, short_take in readings:
        long_time, long_paused, _ = long_take
        short_time, short_paused, short_ended = short_take
        assert not long_paused
        assert short_paused or short_ended
        assert short_time == pytest.approx(5.0, abs=0.15)
        long_times.append(long_time)
        short_times.append(short_time)

    assert all(before < after for before, after in pairwise(long_times)), long_times
    assert all(time == short_times[0] for time in short_times), short_times
    _no_errors(opened)
    return short_times[-1]


def test_p1_short_take_rests_at_end_while_long_take_plays(uneven_page: OpenedPage) -> None:
    _play_past_short_take(uneven_page)


def test_p2_switching_to_short_take_does_not_restart_it(uneven_page: OpenedPage) -> None:
    page = uneven_page[0]
    short_end = _play_past_short_take(uneven_page)
    page.keyboard.press("2")
    page.wait_for_timeout(500)
    _active(page, 2)
    assert sample(page, 250, 1)[0][1][0] == short_end

    page.keyboard.press("1")
    page.wait_for_timeout(500)
    _active(page, 1)
    assert not sample(page, 250, 1)[0][0][3]
    _no_errors(uneven_page)


def test_p3_rejects_loop_outside_active_short_take(uneven_page: OpenedPage) -> None:
    page = uneven_page[0]
    page.keyboard.press("2")
    _drag_view(page, 1, 8.0, 9.0)
    page.keyboard.press("Escape")
    page.keyboard.press("L")

    status = page.locator("#loop-status")
    assert status.get_attribute("data-active") == "false"
    assert "outside" in (status.text_content() or "")
    before = sample(page, 250, 1)[0][1][0]
    page.keyboard.press("Space")
    page.wait_for_function(
        """before => {
            const video = document.querySelector('.take[data-index="2"] video');
            return !video.paused && video.currentTime > before;
        }""",
        arg=before,
    )
    _no_errors(uneven_page)


def test_p4_switching_to_short_take_disables_running_loop(uneven_page: OpenedPage) -> None:
    page = uneven_page[0]
    _drag_view(page, 1, 8.0, 9.0)
    page.keyboard.press("Escape")
    page.keyboard.press("L")
    status = page.locator("#loop-status")
    assert status.get_attribute("data-active") == "true"

    page.keyboard.press("Space")
    page.wait_for_timeout(1000)
    page.keyboard.press("2")

    assert status.get_attribute("data-active") == "false"
    _active(page, 2)
    _no_errors(uneven_page)


def test_p5_saved_comment_survives_failed_read_back(
    live_page: tuple[OpenedPage, str, Path],
) -> None:
    opened, url, root = live_page
    page = opened[0]
    comments_url = url + "comments"
    post_seen = False

    def fail_read_back(route: Route) -> None:
        nonlocal post_seen
        method = route.request.method
        if method == "POST":
            post_seen = True
        if method == "GET" and post_seen:
            route.abort()
        else:
            route.continue_()

    page.route(comments_url, fail_read_back)
    with page.expect_event(
        "requestfailed", predicate=lambda req: req.url == comments_url and req.method == "GET"
    ):
        save_on_page(page, 1, 2.0, 2.0, "question", "kept")

    assert page.locator("#comment-status").get_attribute("data-state") == "saved"
    assert page.locator("#comment-box").is_hidden()
    (record,) = comment_files(root)
    assert record["text"] == "kept"

    page.unroute(comments_url, fail_read_back)
    page.wait_for_function(
        """() => document.querySelectorAll('.comment-item').length === 1
            && document.querySelectorAll('.comment-mark').length === 1""",
        timeout=8000,
    )


def test_p6_comment_fields_disabled_only_while_saving(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, url, _ = live_page
    page = opened[0]
    comments_url = url + "comments"
    snapshots = []

    def delay_post(route: Route) -> None:
        if route.request.method == "POST":
            deadline = monotonic() + 0.7
            snapshots.append(
                page.evaluate(
                    """() => [
                        document.querySelector('#comment-status').dataset.state,
                        document.querySelector('#comment-text').disabled,
                        document.querySelector('#comment-kind').disabled
                    ]"""
                )
            )
            remaining_ms = (deadline - monotonic()) * 1000
            if remaining_ms > 0:
                page.wait_for_timeout(remaining_ms)
        route.continue_()

    page.route(comments_url, delay_post)
    _click_served_view(page, comment_round, 1, 2.0)
    text = page.locator("#comment-text")
    text.press_sequentially("slow")
    with page.expect_response(
        lambda response: response.url == comments_url and response.request.method == "POST"
    ):
        page.locator("#comment-save").click()

    assert snapshots == [["saving", True, True]]
    page.locator('#comment-status[data-state="saved"]').wait_for(state="attached")
    assert text.is_enabled()
    assert page.locator("#comment-kind").is_enabled()
    assert text.input_value() == ""


def test_p7_save_reads_again_after_in_flight_poll(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, url, root = live_page
    page = opened[0]
    comments_url = url + "comments"
    held_reads: dict[float, float | None] = {}
    posts: list[float] = []

    def hold_get(route: Route) -> None:
        method = route.request.method
        if method == "GET":
            started = monotonic()
            held_reads[started] = None
            page.wait_for_timeout(2500)
            held_reads[started] = monotonic()
        elif method == "POST":
            posts.append(monotonic())
        route.continue_()

    page.route(comments_url, hold_get)
    page.reload(wait_until="load")
    loaded = monotonic()
    periodic_start = None
    while periodic_start is None:
        assert monotonic() < loaded + 15, "no periodic GET /comments began after page load"
        page.wait_for_timeout(25)
        periodic_start = next((start for start in held_reads if start >= loaded + 4), None)

    # Reuse P5's save gesture, but observe saved and the rendered result with one 9 s wait.
    _click_served_view(page, comment_round, 1, 2.0)
    page.locator("#comment-kind").select_option("question")
    page.locator("#comment-text").fill("meets a re-read")
    page.locator("#comment-save").click()
    at_saved = page.wait_for_function(
        """() => {
            const status = document.querySelector('#comment-status');
            if (status?.dataset.state !== 'saved') return false;
            const marks = document.querySelectorAll('.comment-mark');
            const items = document.querySelectorAll('.comment-item');
            const text = items[0]?.querySelector('.comment-item-text')?.textContent ?? null;
            return [marks.length, items.length, text];
        }""",
        timeout=9000,
    ).json_value()
    assert at_saved == [1, 1, "meets a re-read"]

    (post_started,) = posts
    periodic_release = held_reads[periodic_start]
    assert periodic_release is not None and periodic_start <= post_started < periodic_release
    # A held GET can see the new record when released, so the DOM alone cannot prove a fresh read.
    assert any(
        start >= periodic_release and release is not None for start, release in held_reads.items()
    )
    (record,) = comment_files(root)
    assert record["text"] == "meets a re-read"
    _no_errors(opened)
