from dataclasses import FrozenInstanceError, is_dataclass
from math import inf, nextafter

import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st


def _at_least(start: float, gap: float) -> float:
    end = start + gap
    return nextafter(end, inf) if end - start < gap else end


@st.composite
def ruler_bounds(draw: st.DrawFn) -> tuple[int, int, float, float]:
    x0 = draw(st.integers(0, 9999))
    x1 = draw(st.integers(x0 + 1, 10000))
    t0 = draw(st.floats(0.0, 100000.0 - 0.001))
    t1 = draw(st.floats(_at_least(t0, 0.001), 100000.0))
    return x0, x1, t0, t1


def test_r1_time_to_x() -> None:
    from sal.views.ruler import Ruler, time_to_x

    ruler = Ruler(80, 1560, 0.0, 70.0)
    assert [time_to_x(t, ruler) for t in (0.0, 70.0, 35.0)] == [80.0, 1560.0, 820.0]


def test_r2_x_to_time() -> None:
    from sal.views.ruler import Ruler, x_to_time

    ruler = Ruler(80, 1560, 0.0, 70.0)
    assert [x_to_time(x, ruler) for x in (80, 1560, 820)] == [0.0, 70.0, 35.0]


def test_r3_invalid_bounds() -> None:
    from sal.views.ruler import Ruler

    for bounds in [(10, 10, 0, 1), (10, 5, 0, 1), (0, 10, 2.0, 2.0), (0, 10, 3.0, 1.0)]:
        with pytest.raises(ValueError):
            Ruler(*bounds)


def test_r4_frozen_dataclass() -> None:
    from sal.views.ruler import Ruler

    ruler = Ruler(80, 1560, 0.0, 70.0)
    assert is_dataclass(ruler)
    with pytest.raises(FrozenInstanceError):
        ruler.x0 = 1


@seed(0)
@settings(max_examples=200, deadline=None)
@given(bounds=ruler_bounds(), data=st.data())
def test_r5_round_trip(bounds: tuple[int, int, float, float], data: st.DataObject) -> None:
    from sal.views.ruler import Ruler, time_to_x, x_to_time

    _, _, t0, t1 = bounds
    t = data.draw(st.floats(t0, t1), label="t")
    ruler = Ruler(*bounds)
    assert abs(x_to_time(time_to_x(t, ruler), ruler) - t) <= 1e-6 * max(1.0, t1)


@seed(0)
@settings(max_examples=200, deadline=None)
@given(bounds=ruler_bounds(), data=st.data())
def test_r6_strictly_increasing(bounds: tuple[int, int, float, float], data: st.DataObject) -> None:
    from sal.views.ruler import Ruler, time_to_x

    _, _, t0, t1 = bounds
    gap = 1e-6 * (t1 - t0)
    latest_start = t1 - gap
    if t1 - latest_start < gap:
        latest_start = nextafter(latest_start, -inf)
    ta = data.draw(st.floats(t0, latest_start), label="ta")
    tb = data.draw(st.floats(_at_least(ta, gap), t1), label="tb")
    ruler = Ruler(*bounds)
    assert time_to_x(ta, ruler) < time_to_x(tb, ruler)


@seed(0)
@settings(max_examples=200, deadline=None)
@given(bounds=ruler_bounds())
def test_r7_endpoints_and_midpoint(bounds: tuple[int, int, float, float]) -> None:
    from sal.views.ruler import Ruler, time_to_x

    x0, x1, t0, t1 = bounds
    ruler = Ruler(*bounds)
    assert time_to_x(t0, ruler) == x0
    assert time_to_x(t1, ruler) == x1
    assert abs(time_to_x((t0 + t1) / 2, ruler) - (x0 + x1) / 2) <= 1e-6 * (x1 - x0)


@seed(0)
@settings(max_examples=200, deadline=None)
@given(bounds=ruler_bounds(), data=st.data())
def test_r8_inside_ruler(bounds: tuple[int, int, float, float], data: st.DataObject) -> None:
    from sal.views.ruler import Ruler, time_to_x

    x0, x1, t0, t1 = bounds
    t = data.draw(st.floats(t0, t1), label="t")
    assert x0 <= time_to_x(t, Ruler(*bounds)) <= x1
