import os
from pathlib import Path

from conftest import ROUND_LABELS, run_round_new, run_sal


def _cold_font_cache(root: Path) -> dict[str, str]:
    config = root / "mpl-config"
    config.mkdir()
    return {**os.environ, "MPLCONFIGDIR": str(config)}


def test_e1_first_view_render_has_no_stderr(tmp_path: Path, step_wav: Path) -> None:
    out = tmp_path / "view"
    result = run_sal(
        "view",
        "render",
        "audio_basic",
        "--input",
        str(step_wav),
        "--out",
        str(out),
        env=_cold_font_cache(tmp_path),
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert (out / "view.png").is_file()
    assert (out / "view.json").is_file()
    assert result.stderr == ""


def test_e2_first_round_build_has_no_stderr(
    tmp_path: Path, take_low_mp4: Path, take_high_mp4: Path
) -> None:
    takes = [
        f"{path}::{label}"
        for path, label in zip((take_low_mp4, take_high_mp4), ROUND_LABELS, strict=True)
    ]
    created = run_round_new(tmp_path, takes)
    assert created.returncode == 0, created.stderr
    result = run_sal("round", "build", "rounds/r1", env=_cold_font_cache(tmp_path), cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
