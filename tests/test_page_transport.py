from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import comment_files, comment_paths, make_round, read_json
from test_page import (
    OpenedPage,
    _active,
    _click_view,
    _comment_attributes,
    _fresh_page,
    _no_errors,
    _row,
    state,
    times,
)
from test_page import page as page
from test_page_save import _click_view as _click_served_view
from test_page_save import _live_page, _wait_for_paint

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.browser

FIRST_VIDEO = '.take[data-index="1"] video'


def _seek_with_chart(page: Page, t: float) -> None:
    _click_view(page, 1, t)
    page.keyboard.press("Escape")


def _wait_for_paused_transport(page: Page) -> None:
    page.wait_for_function(
        """selector =>
            document.querySelector(selector).paused
            && document.querySelector('#play').dataset.state === 'paused'""",
        arg=FIRST_VIDEO,
        timeout=500,
    )


def test_c1_play_button_and_space_toggle_transport(page: OpenedPage) -> None:
    opened = page[0]
    play = opened.locator("#play")
    assert play.evaluate("element => element.localName") == "button"
    assert play.get_attribute("data-state") == "paused"

    play.click()
    opened.wait_for_timeout(1000)
    assert not state(opened)[0]["paused"]
    assert play.get_attribute("data-state") == "playing"

    play.click()
    _wait_for_paused_transport(opened)
    opened.keyboard.press("Space")
    opened.locator('#play[data-state="playing"]').wait_for(state="attached")
    opened.keyboard.press("Space")
    _wait_for_paused_transport(opened)


def test_c2_time_readout_formats_position_and_duration(page: OpenedPage) -> None:
    opened = page[0]
    _seek_with_chart(opened, 7.0)
    opened.wait_for_function(
        "pattern => new RegExp(pattern).test(document.querySelector('#time').innerText)",
        arg=r"^0:07\.[0-9] / 0:12\.0$",
    )


def test_c3_time_readout_follows_playback(page: OpenedPage) -> None:
    opened = page[0]
    opened.keyboard.press("Space")
    readout = opened.locator("#time")
    first = readout.inner_text()
    opened.wait_for_timeout(600)
    assert readout.inner_text() != first


def test_c4_arrows_seek_by_one_second_without_comment(page: OpenedPage) -> None:
    opened = page[0]
    _seek_with_chart(opened, 5.0)
    opened.keyboard.press("ArrowRight")
    assert times(opened) == pytest.approx([6.0, 6.0], abs=0.1)
    assert opened.locator("#comment-box").is_hidden()

    opened.keyboard.press("ArrowLeft")
    opened.keyboard.press("ArrowLeft")
    assert times(opened) == pytest.approx([4.0, 4.0], abs=0.1)


def test_c5_shift_arrows_seek_by_five_and_clamp(page: OpenedPage) -> None:
    opened = page[0]
    _seek_with_chart(opened, 5.0)
    opened.keyboard.press("Shift+ArrowRight")
    assert times(opened) == pytest.approx([10.0, 10.0], abs=0.1)

    opened.keyboard.press("Shift+ArrowRight")
    first, second = opened.evaluate(
        """() => Array.from(document.querySelectorAll('video'),
            video => [video.currentTime, video.duration])"""
    )
    for current, duration in (first, second):
        assert duration - 0.15 <= current <= duration

    for _ in range(3):
        opened.keyboard.press("Shift+ArrowLeft")
    assert times(opened) == pytest.approx([0.0, 0.0], abs=0.1)


def test_c6_comment_text_focus_blocks_seek_and_comment_keys(page: OpenedPage) -> None:
    opened = page[0]
    _click_view(opened, 1, 5.0)
    text = opened.locator("#comment-text")
    text.click()
    before = times(opened)

    opened.keyboard.press("ArrowRight")
    assert times(opened) == pytest.approx(before, abs=0.05)
    opened.keyboard.press("ArrowLeft")
    assert times(opened) == pytest.approx(before, abs=0.05)
    text.press_sequentially("cC")

    assert times(opened) == pytest.approx(before, abs=0.05)
    assert text.input_value() == "cC"


def test_c7_clicking_active_video_toggles_playback(page: OpenedPage) -> None:
    opened = page[0]
    video = opened.locator(FIRST_VIDEO)
    video.click()
    opened.wait_for_function(
        "selector => !document.querySelector(selector).paused", arg=FIRST_VIDEO, timeout=1000
    )

    video.click()
    opened.wait_for_function(
        "selector => document.querySelector(selector).paused", arg=FIRST_VIDEO, timeout=500
    )


def test_c8_clicking_take_label_switches_active_take(page: OpenedPage) -> None:
    opened = page[0]
    _row(opened, 2).locator(".take-label").click()
    _active(opened, 2)
    first, second = state(opened)
    assert first["muted"] and not second["muted"]


def test_c9_c_opens_box_at_current_time(page: OpenedPage, plain_round: Path) -> None:
    opened = page[0]
    _seek_with_chart(opened, 3.0)
    opened.keyboard.press("Space")
    opened.wait_for_timeout(1000)
    t = times(opened)[0]
    opened.keyboard.press("c")

    start, end, sha256 = _comment_attributes(opened)
    assert start == end
    assert float(start) == pytest.approx(t, abs=0.25)
    expected_sha256 = read_json(plain_round / "build" / "page.json")["takes"][0]["sha256"]
    assert sha256 == expected_sha256
    assert opened.locator("#comment-text").evaluate("text => document.activeElement === text")

    opened.wait_for_timeout(500)
    assert not state(opened)[0]["paused"]


def test_c10_legend_lists_shortcuts(page: OpenedPage) -> None:
    legend = page[0].locator(".legend").inner_text()
    for key in ("Space", "1", "L", "C", "Esc", "←", "→"):
        assert key in legend


def test_c11_gain_rounding_does_not_show_negative_zero(
    browser_name: str, tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    round_dir = make_round(tmp_path, (take_low_mp4, take_high_mp4))
    index = round_dir / "build" / "index.html"
    with _fresh_page(browser_name, round_dir) as (opened, _, _, _):
        first_gain = _row(opened, 1).locator(".take-gain").inner_text()
        original = index.read_text(encoding="utf-8")
        script = re.search(
            r"""<script\b[^>]*\s+id\s*=\s*["']page-data["'][^>]*>(.*?)</script\s*>""",
            original,
            re.DOTALL,
        )
        assert script is not None, "missing #page-data script"
        data = json.loads(script.group(1))
        data["takes"][1]["gain_db"] = -0.01
        start, end = script.span(1)
        changed = json.dumps(data, ensure_ascii=False, allow_nan=False)
        index.write_text(original[:start] + changed + original[end:], encoding="utf-8")
        opened.reload()

        assert _row(opened, 2).locator(".take-gain").inner_text() == "0.0 dB"
        assert _row(opened, 1).locator(".take-gain").inner_text() == first_gain


def test_c12_control_enter_saves_served_comment(browser_name: str, comment_round: Path) -> None:
    with _live_page(browser_name, comment_round) as (opened, _, root):
        page = opened[0]
        _click_served_view(page, comment_round, 1, 2.0)
        page.locator("#comment-kind").select_option("question")
        text = page.locator("#comment-text")
        text.press_sequentially("why here")
        text.press("Control+Enter")

        page.locator('#comment-status[data-state="saved"]').wait_for(state="attached")
        assert page.locator(".comment-mark").count() == 1
        (record,) = comment_files(root)
        assert record["text"] == "why here"


def test_c13_control_enter_keeps_offline_draft(page: OpenedPage, plain_round: Path) -> None:
    opened = page[0]
    _click_view(opened, 1, 2.0)
    text = opened.locator("#comment-text")
    text.press_sequentially("x")
    text.press("Control+Enter")
    _wait_for_paint(opened)

    assert opened.locator("#comment-status").get_attribute("data-state") == "offline"
    assert opened.locator("#comment-box").is_visible()
    assert text.input_value() == "x"
    assert opened.locator(".comment-mark").count() == 0
    assert comment_paths(plain_round.parent.parent) == []
    _no_errors(page)
