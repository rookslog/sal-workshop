import numpy as np
import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays


@st.composite
def samples_and_bins(draw: st.DrawFn) -> tuple[np.ndarray, int]:
    rows = draw(st.integers(1, 2000))
    shape = draw(st.sampled_from([(rows,), (rows, 1), (rows, 2)]))
    samples = draw(arrays(np.float64, shape, elements=st.floats(-1.0, 1.0)))
    bins = draw(st.integers(1, rows))
    return samples, bins


def test_p1_mono_peaks() -> None:
    from sal.views.audio import peak_bins

    result = peak_bins(np.array([0.1, -0.5, 0.2, 0.3]), 2)
    np.testing.assert_array_equal(result, [0.5, 0.3])


def test_p2_larger_channel_wins() -> None:
    from sal.views.audio import peak_bins

    samples = np.array([[0.0, 0.1], [0.0, -0.7], [0.0, 0.2], [0.0, 0.3]])
    np.testing.assert_array_equal(peak_bins(samples, 2), [0.7, 0.3])


def test_p3_uneven_bin_boundaries() -> None:
    from sal.views.audio import peak_bins

    samples = np.arange(1.0, 8.0) * 0.1
    np.testing.assert_array_equal(peak_bins(samples, 3), samples[[1, 3, 6]])


def test_p4_invalid_bin_counts() -> None:
    from sal.views.audio import peak_bins

    samples = np.array([0.1, -0.5, 0.2, 0.3])
    for bins in (0, 5):
        with pytest.raises(ValueError):
            peak_bins(samples, bins)


@seed(0)
@settings(max_examples=200, deadline=None)
@given(case=samples_and_bins())
def test_p5_shape_maximum_and_nonnegative(case: tuple[np.ndarray, int]) -> None:
    from sal.views.audio import peak_bins

    samples, bins = case
    result = peak_bins(samples, bins)
    assert result.shape == (bins,)
    assert np.max(result) == np.max(np.abs(samples))
    assert np.all(result >= 0)


@seed(0)
@settings(max_examples=200, deadline=None)
@given(case=samples_and_bins(), k=st.floats(0.0, 4.0))
def test_p6_scaling(case: tuple[np.ndarray, int], k: float) -> None:
    from sal.views.audio import peak_bins

    samples, bins = case
    np.testing.assert_allclose(
        peak_bins(k * samples, bins), k * peak_bins(samples, bins), atol=1e-9, rtol=0
    )
