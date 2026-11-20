from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import pytest
from conftest import (
    ROUND_LABELS,
    comment_files,
    comment_paths,
    make_round,
    read_json,
    run_sal,
    sample,
    save_on_page,
    served,
)
from test_page import OpenedPage, _active, _no_errors, _row, state, times
from test_page import page as page
from test_page_save import _click_view, _live_page, _page_takes, _view_x, _wait_for_paint
from test_page_save import live_page as live_page
from test_page_stage import _assert_inside_window, _layout

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page, Response

pytestmark = pytest.mark.browser

ITEMS = "#comments .comment-item"
LITERAL_TEXT = '<b id="injected">x</b> & <script>window.__injected = 1</script>'


def _field(item: Locator, name: str) -> str | None:
    return item.locator(f".comment-item-{name}").text_content()


def _assert_two_items(page: Page, round_dir: Path, ids: tuple[str, str]) -> None:
    first, second = page.locator(ITEMS).all()
    first_take, second_take = _page_takes(round_dir)
    assert (first.get_attribute("data-id"), second.get_attribute("data-id")) == ids
    assert _field(first, "kind") == "question"
    assert _field(first, "take") == first_take["shown"]
    assert re.fullmatch(r"0:02\.[0-9]", _field(first, "time") or "")
    assert _field(first, "author") == "operator"
    assert _field(first, "text") == "why here"

    assert _field(second, "kind") == "flag"
    assert _field(second, "take") == second_take["shown"]
    assert re.fullmatch(r"0:05\.[0-9]–0:07\.[0-9]", _field(second, "time") or "")
    assert _field(second, "text") == "too loud"


def _save_two_items(page: Page, round_dir: Path, root: Path) -> tuple[str, str]:
    save_on_page(page, 1, 2.0, 2.0, "question", "why here")
    save_on_page(page, 2, 5.0, 7.0, "flag", "too loud")
    paths = comment_paths(root)
    assert len(paths) == 2
    ids_by_text = {read_json(path)["text"]: path.stem for path in paths}
    ids = (ids_by_text["why here"], ids_by_text["too loud"])
    _assert_two_items(page, round_dir, ids)
    return ids


def _wait_for_initial_read(page: Page) -> None:
    page.wait_for_function(
        """() => {
            const url = new URL('/comments', location.href).href;
            return performance.getEntriesByName(url).length > 0;
        }"""
    )
    _wait_for_paint(page)


def _watch_comment_reads(page: Page, url: str) -> list[str]:
    reads: list[str] = []
    comments_url = urljoin(url, "comments")

    def record_read(response: Response) -> None:
        if response.url == comments_url and response.request.method == "GET" and response.ok:
            reads.append(response.url)

    page.on("response", record_read)
    return reads


def _assert_literal_text(page: Page) -> None:
    item = page.locator(ITEMS)
    item.wait_for(state="attached")
    assert _field(item, "text") == LITERAL_TEXT
    assert page.evaluate(
        "() => document.querySelector('#injected') === null && window.__injected === undefined"
    )


def test_l1_served_page_has_empty_comment_list(live_page: tuple[OpenedPage, str, Path]) -> None:
    opened, _, _ = live_page
    page = opened[0]
    _wait_for_initial_read(page)
    assert page.locator("#comments").count() == 1
    assert page.locator(ITEMS).count() == 0


def test_l2_saved_comments_show_fields_and_file_ids(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, _, root = live_page
    _save_two_items(opened[0], comment_round, root)


def test_l3_reload_keeps_the_same_items_in_order(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, _, root = live_page
    page = opened[0]
    ids = _save_two_items(page, comment_round, root)
    page.reload()
    _wait_for_initial_read(page)
    _assert_two_items(page, comment_round, ids)


def test_l4_clicking_a_stretch_seeks_selects_and_enables_loop(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, _, root = live_page
    page = opened[0]
    _save_two_items(page, comment_round, root)
    page.keyboard.press("Escape")
    page.locator(ITEMS).nth(1).click()
    _wait_for_paint(page)

    _active(page, 2)
    assert times(page) == pytest.approx([5.0, 5.0], abs=0.1)
    selection = _row(page, 2).locator(".selection").bounding_box()
    assert selection is not None
    assert selection["x"] == pytest.approx(_view_x(page, comment_round, 2, 5.0), abs=3)
    assert selection["x"] + selection["width"] == pytest.approx(
        _view_x(page, comment_round, 2, 7.0), abs=3
    )
    assert page.locator("#comment-box").is_hidden()
    assert all(video["paused"] for video in state(page))

    page.keyboard.press("L")
    assert page.locator("#loop-status").get_attribute("data-active") == "true"


def test_l5_cli_answer_appears_in_the_answered_item(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, _, root = live_page
    page = opened[0]
    first_id, second_id = _save_two_items(page, comment_round, root)
    result = run_sal(
        "comment",
        "answer",
        first_id,
        "--text",
        "because of the cut",
        "--author",
        "agent:test/model",
        cwd=root,
    )
    assert result.returncode == 0, result.stderr
    (answer_record,) = [record for record in comment_files(root) if record["kind"] == "answer"]
    page.wait_for_function(
        """([firstId, secondId, answerId]) => {
            const items = document.querySelectorAll('#comments .comment-item');
            if (items.length !== 2) return false;
            const [first, second] = items;
            const answers = first.querySelectorAll('.comment-answer');
            const answer = answers[0];
            const author = answer?.querySelector('.comment-answer-author');
            const text = answer?.querySelector('.comment-answer-text');
            return first.dataset.id === firstId && second.dataset.id === secondId
                && answers.length === 1 && answer.dataset.id === answerId
                && author?.textContent === 'agent:test/model'
                && text?.textContent === 'because of the cut'
                && second.querySelector('.comment-answer') === null;
        }""",
        arg=[first_id, second_id, answer_record["id"]],
        timeout=8000,
    )


def test_l6_comment_words_stay_literal_after_save_and_reload(
    live_page: tuple[OpenedPage, str, Path],
) -> None:
    opened, _, _ = live_page
    page = opened[0]
    save_on_page(page, 1, 3.0, 3.0, "question", LITERAL_TEXT)
    _assert_literal_text(page)
    page.reload()
    _assert_literal_text(page)


def test_l7_replacement_appears_and_old_item_leaves(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, _, root = live_page
    page = opened[0]
    old_id, _ = _save_two_items(page, comment_round, root)
    first_take = _page_takes(comment_round)[0]
    result = run_sal(
        "comment",
        "add",
        "--round",
        comment_round.name,
        "--take",
        first_take["sha256"],
        "--span",
        "2:2",
        "--kind",
        "question",
        "--text",
        "why exactly here",
        "--replaces",
        old_id,
        cwd=root,
    )
    assert result.returncode == 0, result.stderr
    new_id = json.loads(result.stdout)["comment"]
    page.wait_for_function(
        """([oldId, newId]) => {
            const items = Array.from(document.querySelectorAll('#comments .comment-item'));
            const replacement = items.find(item => item.dataset.id === newId);
            const text = replacement?.querySelector('.comment-item-text');
            return items.length === 2
                && !items.some(item => item.dataset.id === oldId)
                && text?.textContent === 'why exactly here';
        }""",
        arg=[old_id, new_id],
        timeout=8000,
    )


def test_l8_blind_comment_shows_only_the_letter(
    browser_name: str, blind_comment_round: Path
) -> None:
    with _live_page(browser_name, blind_comment_round) as (opened, _, _):
        page = opened[0]
        labels = page.locator(".take-label").all_text_contents()
        assert "B" in labels
        save_on_page(page, labels.index("B") + 1, 4.0, 4.0, "verdict", "this one")

        (item,) = page.locator(ITEMS).all()
        assert _field(item, "take") == "B"
        body = page.locator("body").inner_text()
        assert all(label not in body for label in ROUND_LABELS)


def test_l9_offline_list_is_empty_and_requests_are_local(page: OpenedPage) -> None:
    opened, _, _, requests = page
    assert opened.locator("#comments").count() == 1
    assert opened.locator(ITEMS).count() == 0
    assert all(url.startswith("file://") for url in requests), requests


def test_l10_list_does_not_show_another_rounds_comment(
    browser_name: str, tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    takes = (take_low_mp4, take_high_mp4)
    first_round = make_round(tmp_path, takes, round_id="first")
    second_round = make_round(tmp_path, takes, round_id="second")
    with _live_page(browser_name, first_round) as (opened, _, root):
        save_on_page(opened[0], 1, 2.0, 2.0, "question", "why here")
        (record,) = comment_files(root)
        assert record["round"] == first_round.name

    with _live_page(browser_name, second_round) as (opened, _, _):
        page = opened[0]
        _wait_for_initial_read(page)
        assert page.locator("#comments").count() == 1
        assert page.locator(ITEMS).count() == 0


def test_l11_long_list_scrolls_without_displacing_video(
    browser_name: str, comment_round: Path
) -> None:
    from playwright.sync_api import sync_playwright

    root = comment_round.parent.parent
    sha256 = _page_takes(comment_round)[0]["sha256"]
    for index in range(30):
        t = f"{index * 0.3:g}"
        result = run_sal(
            "comment",
            "add",
            "--round",
            comment_round.name,
            "--take",
            sha256,
            "--span",
            f"{t}:{t}",
            "--kind",
            "flag",
            "--text",
            f"flag {index + 1}",
            cwd=root,
        )
        assert result.returncode == 0, result.stderr

    with served(comment_round) as (url, _), sync_playwright() as playwright:
        options = {"args": ["--mute-audio"]} if browser_name == "chromium" else {}
        browser = getattr(playwright, browser_name).launch(headless=True, **options)
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 900}, service_workers="block"
            )
            context.route(re.compile(rf"^(?!{re.escape(url)})"), lambda route: route.abort())
            context.route_web_socket("**/*", lambda socket: socket.close())
            page = context.new_page()
            page.goto(url)
            _wait_for_initial_read(page)

            assert page.locator(ITEMS).count() == 30
            layout = _layout(page)
            video = layout["videos"][0]
            assert video["width"] >= 480
            _assert_inside_window(video, layout["viewport"])
            comments = page.locator("#comments")
            assert comments.evaluate("list => list.scrollHeight > list.clientHeight")
            assert layout["scrollWidth"] <= layout["clientWidth"]
        finally:
            browser.close()


def test_l12_polling_preserves_the_open_draft(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, url, root = live_page
    page = opened[0]
    _save_two_items(page, comment_round, root)
    comments = page.locator("#comments")
    comments.click()
    comments.evaluate(
        """list => {
            if (list.scrollHeight > list.clientHeight) {
                list.scrollTop = list.scrollHeight;
            }
        }"""
    )
    _click_view(page, comment_round, 1, 9.0)
    text = page.locator("#comment-text")
    text.press_sequentially("half a thought")
    box = page.locator("#comment-box")
    start = box.get_attribute("data-start")
    assert start is not None

    reads = _watch_comment_reads(page, url)
    page.wait_for_timeout(11000)

    assert len(reads) >= 2
    assert box.is_visible()
    assert text.input_value() == "half a thought"
    assert box.get_attribute("data-start") == start
    _no_errors(opened)


def test_l13_polling_does_not_interrupt_playback(
    live_page: tuple[OpenedPage, str, Path], comment_round: Path
) -> None:
    opened, url, root = live_page
    page = opened[0]
    _save_two_items(page, comment_round, root)
    before = sample(page, 250, 1)[0][0][0]
    page.keyboard.press("Space")
    reads = _watch_comment_reads(page, url)
    page.wait_for_timeout(6000)
    current, _, paused, _ = sample(page, 250, 1)[0][0]

    assert reads
    assert not paused
    assert current - before == pytest.approx(6.0, abs=1.0)


def test_l14_sorts_by_start_not_save_order(live_page: tuple[OpenedPage, str, Path]) -> None:
    opened, _, _ = live_page
    page = opened[0]
    save_on_page(page, 1, 8.0, 8.0, "flag", "late")
    save_on_page(page, 1, 1.0, 1.0, "flag", "early")

    items = page.locator(ITEMS)
    assert [_field(item, "text") for item in items.all()] == ["early", "late"]

    page.reload()
    _wait_for_initial_read(page)
    assert [_field(item, "text") for item in items.all()] == ["early", "late"]
