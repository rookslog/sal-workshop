from itertools import pairwise

import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st

type Intervals = list[tuple[float, float]]

_endpoint = st.integers(0, 100_000).map(lambda milliseconds: milliseconds / 1000)
_intervals = st.lists(st.tuples(_endpoint, _endpoint), max_size=8)


def test_t1_partial_last_tile() -> None:
    from sal.views.video import tile_layout

    tiles = tile_layout(80, 1480, 160)
    assert len(tiles) == 10
    assert tiles[0] == (80, 160)
    assert tiles[-1] == (1520, 40)


def test_t2_even_tiles() -> None:
    from sal.views.video import tile_layout

    assert tile_layout(0, 320, 80) == [(0, 80), (80, 80), (160, 80), (240, 80)]


def test_t3_box_narrower_than_tile() -> None:
    from sal.views.video import tile_layout

    assert tile_layout(5, 3, 10) == [(5, 3)]


def test_t4_zero_widths() -> None:
    from sal.views.video import tile_layout

    for box_width, tile_width in [(0, 10), (10, 0)]:
        with pytest.raises(ValueError):
            tile_layout(0, box_width, tile_width)


@seed(0)
@settings(max_examples=200, deadline=None)
@given(
    box_x=st.integers(0, 5000),
    box_width=st.integers(1, 5000),
    tile_width=st.integers(1, 5000),
)
def test_t5_tiles_fill_box(box_x: int, box_width: int, tile_width: int) -> None:
    from sal.views.video import tile_layout

    tiles = tile_layout(box_x, box_width, tile_width)
    assert tiles[0][0] == box_x
    for (x, width), (next_x, _) in pairwise(tiles):
        assert next_x == x + width
    assert sum(width for _, width in tiles) == box_width
    assert all(width == tile_width for _, width in tiles[:-1])
    assert 1 <= tiles[-1][1] <= tile_width


def test_m1_merge_touching_and_overlapping_intervals() -> None:
    from sal.views.video import merge_intervals

    assert merge_intervals([(5, 6), (1, 2), (2, 3), (5.5, 7), (9, 9), (4, 3)]) == [
        (1, 3),
        (5, 7),
    ]


def test_s1_subtract_two_intervals() -> None:
    from sal.views.video import subtract_intervals

    assert subtract_intervals([(0, 10)], [(2, 3), (5, 6)]) == [(0, 2), (3, 5), (6, 10)]


def test_s2_subtract_drops_short_remainders() -> None:
    from sal.views.video import subtract_intervals

    assert subtract_intervals([(4.9, 6.2)], [(5, 6)], min_length=0.5) == []


def test_s3_subtract_keeps_long_remainders() -> None:
    from sal.views.video import subtract_intervals

    assert subtract_intervals([(4, 7)], [(5, 6)], min_length=0.5) == [(4, 5), (6, 7)]


def test_s4_subtract_empty_list() -> None:
    from sal.views.video import subtract_intervals

    assert subtract_intervals([(1, 2)], []) == [(1, 2)]


def test_c1_clip_across_two_intervals() -> None:
    from sal.views.video import clip_intervals

    assert clip_intervals([(1, 4), (6, 9)], 3, 7) == [(3, 4), (6, 7)]


def test_c2_clip_touching_endpoint() -> None:
    from sal.views.video import clip_intervals

    assert clip_intervals([(1, 2)], 2, 5) == []


@seed(0)
@settings(max_examples=200, deadline=None)
@given(a=_intervals, b=_intervals)
def test_i1_subtraction_interval_invariants(a: Intervals, b: Intervals) -> None:
    from sal.views.video import merge_intervals, subtract_intervals

    result = subtract_intervals(a, b)
    merged_a = merge_intervals(a)
    merged_b = merge_intervals(b)
    assert result == sorted(result)
    for (_, end), (next_start, _) in pairwise(result):
        assert end < next_start
    for start, end in result:
        assert start < end
        assert any(left <= start < end <= right for left, right in merged_a)
        for left, right in merged_b:
            assert min(end, right) - max(start, left) <= 1e-9


@seed(0)
@settings(max_examples=200, deadline=None)
@given(a=_intervals, b=_intervals)
def test_i2_subtraction_preserves_length_with_overlap(a: Intervals, b: Intervals) -> None:
    from sal.views.video import merge_intervals, subtract_intervals

    merged_a = merge_intervals(a)
    merged_b = merge_intervals(b)
    overlap = sum(
        max(0.0, min(a_end, b_end) - max(a_start, b_start))
        for a_start, a_end in merged_a
        for b_start, b_end in merged_b
    )
    remainder = sum(end - start for start, end in subtract_intervals(a, b))
    original = sum(end - start for start, end in merged_a)
    assert abs(remainder + overlap - original) <= 1e-6


@seed(0)
@settings(max_examples=200, deadline=None)
@given(a=_intervals, b=_intervals)
def test_i3_merge_and_subtraction_are_idempotent(a: Intervals, b: Intervals) -> None:
    from sal.views.video import merge_intervals, subtract_intervals

    remainder = subtract_intervals(a, b)
    assert subtract_intervals(remainder, b) == remainder
    merged_a = merge_intervals(a)
    assert merge_intervals(merged_a) == merged_a


@seed(0)
@settings(max_examples=200, deadline=None)
@given(a=_intervals, start=st.floats(0.0, 100.0, exclude_max=True), data=st.data())
def test_i4_clipping_bounds_and_idempotence(
    a: Intervals, start: float, data: st.DataObject
) -> None:
    from sal.views.video import clip_intervals, merge_intervals

    end = data.draw(st.floats(start, 100.0, exclude_min=True), label="end")
    clipped = clip_intervals(a, start, end)
    merged_a = merge_intervals(a)
    for left, right in clipped:
        assert start <= left <= right <= end
        assert any(a_start <= left <= right <= a_end for a_start, a_end in merged_a)
    assert clip_intervals(clipped, start, end) == clipped
