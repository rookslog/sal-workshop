from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path

import pytest
from conftest import make_round, sample, view_x
from test_page import OpenedPage, _click_view, _drag_view, _fresh_page, _no_errors, _row, times
from test_page_save import _click_view as _click_served_view
from test_page_save import _live_page
from test_page_uneven import _play_past_short_take

pytestmark = pytest.mark.browser


def overlong_take(path: Path, seconds: int, frequency: int) -> Path:
    metadata = path.with_suffix(".ffmetadata")
    metadata.write_text(
        ";FFMETADATA1\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=300000\ntitle=First chapter\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=300000\nEND=600000\ntitle=Second chapter\n",
        encoding="utf-8",
    )
    limit = str(seconds)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-t",
            limit,
            "-i",
            "testsrc2=size=320x180:rate=25",
            "-f",
            "lavfi",
            "-t",
            limit,
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000",
            "-f",
            "ffmetadata",
            "-t",
            limit,
            "-i",
            str(metadata),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map_chapters",
            "2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "25",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        timeout=120,
    )
    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,duration:format=duration",
                "-of",
                "json",
                str(path),
            ],
            timeout=30,
        )
    )
    assert any(
        stream["codec_type"] == "data" and float(stream.get("duration", 0)) >= 599
        for stream in probe["streams"]
    ), probe
    assert float(probe["format"]["duration"]) == pytest.approx(seconds, abs=0.1), probe
    return path


def _make_overlong_round(root: Path, second_seconds: int) -> Path:
    first = overlong_take(root / "first.mp4", 12, 440)
    second = overlong_take(root / "second.mp4", second_seconds, 660)
    return make_round(root, (first, second))


@pytest.fixture(scope="module")
def overlong_round(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _make_overlong_round(tmp_path_factory.mktemp("overlong-round"), 12)


@pytest.fixture(scope="module")
def uneven_overlong_round(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _make_overlong_round(tmp_path_factory.mktemp("uneven-overlong-round"), 5)


@pytest.fixture
def overlong_page(browser_name: str, overlong_round: Path) -> Iterator[OpenedPage]:
    with _fresh_page(browser_name, overlong_round) as opened:
        yield opened


@pytest.fixture
def uneven_overlong_page(browser_name: str, uneven_overlong_round: Path) -> Iterator[OpenedPage]:
    with _fresh_page(browser_name, uneven_overlong_round) as opened:
        yield opened


def _assert_paused_at_measured_end(reading: list[list[float | bool]]) -> None:
    active, other = reading
    assert active[2] and other[2], reading
    assert active[0] == pytest.approx(12.0, abs=0.1), reading
    assert 11.5 <= other[0] <= 12.25, reading


def _play_to_measured_end(opened: OpenedPage) -> None:
    page = opened[0]
    _click_view(page, 1, 9.0)
    page.keyboard.press("Escape")
    page.keyboard.press("Space")

    readings = sample(page, 250, 20)
    for first, second in readings:
        assert first[0] <= 12.25 and second[0] <= 12.25, readings
    last_four = readings[-4:]
    end_times = [video[0] for video in last_four[0]]
    for reading in last_four:
        _assert_paused_at_measured_end(reading)
        assert [video[0] for video in reading] == end_times, last_four
    assert page.locator("#time").inner_text() == "0:12.0 / 0:12.0"
    assert page.locator("#play").get_attribute("data-state") == "paused"
    _no_errors(opened)


def test_ln0_webkit_reports_the_chapter_length(browser_name: str, overlong_round: Path) -> None:
    if browser_name != "webkit":
        return
    with _fresh_page(browser_name, overlong_round) as opened:
        duration = _row(opened[0], 1).locator("video").evaluate("video => video.duration")
        assert duration > 500, f"WebKit no longer reports the long chapter duration: {duration}"


def test_ln1_readout_uses_measured_length(overlong_page: OpenedPage) -> None:
    page = overlong_page[0]
    readout = page.locator("#time")
    assert readout.inner_text() == "0:00.0 / 0:12.0"

    page.keyboard.press("Space")
    page.wait_for_timeout(1500)
    assert readout.inner_text().endswith("/ 0:12.0")


def test_ln2_playback_stops_at_measured_end(overlong_page: OpenedPage) -> None:
    _play_to_measured_end(overlong_page)


def test_ln3_space_restarts_after_measured_end(overlong_page: OpenedPage) -> None:
    _play_to_measured_end(overlong_page)
    page = overlong_page[0]
    page.keyboard.press("Space")
    page.wait_for_timeout(1500)

    assert page.locator("#play").get_attribute("data-state") == "playing"
    current_time, _, paused, _ = sample(page, 250, 1)[0][0]
    assert not paused
    assert 0.5 <= current_time <= 2.5, current_time


def test_ln4_shift_seek_clamps_to_measured_end(overlong_page: OpenedPage) -> None:
    page = overlong_page[0]
    _click_view(page, 1, 11.0)
    page.keyboard.press("Escape")
    page.keyboard.press("Shift+ArrowRight")

    first, second = times(page)
    assert first <= 12.05 and second <= 12.05, (first, second)
    readout = page.locator("#time").inner_text()
    assert readout == "0:12.0 / 0:12.0" or readout.startswith("0:11.9"), readout


def test_ln5_short_take_rests_at_measured_end(uneven_overlong_page: OpenedPage) -> None:
    _play_past_short_take(uneven_overlong_page)


def test_ln6_loop_cannot_run_past_measured_end(uneven_overlong_page: OpenedPage) -> None:
    page = uneven_overlong_page[0]
    page.keyboard.press("2")
    _drag_view(page, 1, 8.0, 9.0)
    page.keyboard.press("Escape")
    page.keyboard.press("L")

    status = page.locator("#loop-status")
    assert status.get_attribute("data-active") == "false"
    assert "outside" in (status.text_content() or "")
    _no_errors(uneven_overlong_page)


def test_ln7_served_playback_stops_at_measured_end(browser_name: str, overlong_round: Path) -> None:
    with _live_page(browser_name, overlong_round) as (opened, _, _):
        page = opened[0]
        _click_served_view(page, overlong_round, 1, 9.0)
        page.keyboard.press("Escape")
        page.keyboard.press("Space")
        page.wait_for_timeout(5000)

        _assert_paused_at_measured_end(sample(page, 250, 1)[0])
        assert page.locator("#time").inner_text() == "0:12.0 / 0:12.0"


@pytest.mark.parametrize("round_fixture", ("overlong_round", "plain_round"))
def test_ln8_near_end_pause_resumes_without_restarting(
    browser_name: str, round_fixture: str, request: pytest.FixtureRequest
) -> None:
    with _fresh_page(browser_name, request.getfixturevalue(round_fixture)) as opened:
        page = opened[0]
        _click_view(page, 1, 11.96)
        page.keyboard.press("Escape")
        assert times(page) == pytest.approx([11.96, 11.96], abs=0.03)

        page.keyboard.press("Space")
        page.wait_for_timeout(150)
        assert times(page)[0] >= 11.9
        page.wait_for_timeout(1500)

        current_time, _, paused, _ = sample(page, 250, 1)[0][0]
        assert paused
        assert current_time == pytest.approx(12.0, abs=0.1)
        assert page.locator("#time").inner_text() == "0:12.0 / 0:12.0"


@pytest.mark.parametrize("round_fixture", ("uneven_overlong_round", "uneven_round"))
def test_ln9_loop_wraps_at_measured_end(
    browser_name: str, round_fixture: str, request: pytest.FixtureRequest
) -> None:
    with _fresh_page(browser_name, request.getfixturevalue(round_fixture)) as opened:
        page = opened[0]
        page.keyboard.press("2")
        _drag_view(page, 1, 4.0, 5.03)
        page.keyboard.press("Escape")

        selection = _row(page, 1).locator(".selection").bounding_box()
        assert selection is not None
        origin = view_x(page, 1, 0.0)
        pixels_per_second = view_x(page, 1, 1.0) - origin
        end = (selection["x"] + selection["width"] - origin) / pixels_per_second
        if not 5.0 < end <= 5.05:
            pytest.skip(f"Drag ended at {end:.4f} s, outside the required (5.0, 5.05] s band")

        page.keyboard.press("L")
        assert page.locator("#loop-status").get_attribute("data-active") == "true"
        page.keyboard.press("Space")
        page.wait_for_timeout(100)
        readings = sample(page, 100, 30)
        short_times = [reading[1][0] for reading in readings]

        assert all(3.9 <= t <= 5.2 for t in short_times), short_times
        assert sum(after < before for before, after in pairwise(short_times)) >= 2, short_times
        assert not readings[-1][1][2], readings[-1]
        _no_errors(opened)
