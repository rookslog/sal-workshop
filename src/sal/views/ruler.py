"""Linear time ruler and tick placement for rendered views."""

from __future__ import annotations

import math
from dataclasses import dataclass

_NICE_STEPS: tuple[float, ...] = (
    0.001,
    0.002,
    0.005,
    0.01,
    0.02,
    0.05,
    0.1,
    0.2,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    15.0,
    20.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
    900.0,
    1800.0,
    3600.0,
)


@dataclass(frozen=True)
class Ruler:
    """Linear mapping between take time (seconds) and horizontal pixels."""

    x0: int
    x1: int
    t0: float
    t1: float

    def __post_init__(self) -> None:
        if isinstance(self.x0, bool) or isinstance(self.x1, bool):
            raise ValueError("x0 and x1 must be integers")
        if self.x1 <= self.x0:
            raise ValueError(f"ruler x1 ({self.x1}) must be greater than x0 ({self.x0})")
        if not (math.isfinite(self.t0) and math.isfinite(self.t1)):
            raise ValueError("ruler t0 and t1 must be finite")
        if self.t1 <= self.t0:
            raise ValueError(f"ruler t1 ({self.t1}) must be greater than t0 ({self.t0})")


def time_to_x(t: float, ruler: Ruler) -> float:
    """Map a time in seconds to a horizontal pixel coordinate."""
    return ruler.x0 + (ruler.x1 - ruler.x0) * ((t - ruler.t0) / (ruler.t1 - ruler.t0))


def x_to_time(x: float, ruler: Ruler) -> float:
    """Map a horizontal pixel coordinate back to a time in seconds."""
    return ruler.t0 + (ruler.t1 - ruler.t0) * ((x - ruler.x0) / (ruler.x1 - ruler.x0))


def format_tick_label(t: float, step: float) -> str:
    """Format a ruler tick timestamp cleanly in m:ss or seconds."""
    if step >= 1.0 and abs(t - round(t)) < 1e-6:
        total_seconds = int(round(t))
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes}:{seconds:02d}"
    text = f"{t:.3f}".rstrip("0").rstrip(".")
    return f"{text}s"


def _ticks_for_step(t0: float, t1: float, step: float) -> list[float]:
    start_index = math.ceil((t0 - 1e-12) / step)
    ticks: list[float] = []
    idx = start_index
    while True:
        candidate = round(idx * step, 3)
        if candidate > t1 + 1e-12:
            break
        if t0 - 1e-12 <= candidate <= t1 + 1e-12:
            clamped = min(max(candidate, round(t0, 3)), round(t1, 3))
            if not ticks or clamped > ticks[-1]:
                ticks.append(clamped)
        idx += 1
    return ticks


def make_ticks(ruler: Ruler) -> list[dict[str, float | str]]:
    """Build at least three evenly spaced labelled ticks within the ruler span."""
    duration = ruler.t1 - ruler.t0
    chosen_step: float | None = None
    chosen_times: list[float] = []

    for step in _NICE_STEPS:
        times = _ticks_for_step(ruler.t0, ruler.t1, step)
        if 5 <= len(times) <= 11:
            chosen_step = step
            chosen_times = times
            break

    if not chosen_times:
        for step in reversed(_NICE_STEPS):
            times = _ticks_for_step(ruler.t0, ruler.t1, step)
            if len(times) >= 3:
                chosen_step = step
                chosen_times = times
                break

    if not chosen_times:
        step = max(0.001, round(duration / 4.0, 3))
        chosen_step = step
        chosen_times = [
            round(ruler.t0 + i * step, 3)
            for i in range(5)
            if round(ruler.t0 + i * step, 3) <= ruler.t1 + 1e-12
        ]

    assert chosen_step is not None
    return [
        {
            "t": t,
            "x": round(time_to_x(t, ruler), 3),
            "label": format_tick_label(t, chosen_step),
        }
        for t in chosen_times
    ]
