"""External media probing, hashing, and reproducible command tracking."""

from __future__ import annotations

import hashlib
import json
import math
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

INPUT_PLACEHOLDER = "<input>"


@dataclass(frozen=True)
class MediaInfo:
    """Probed metadata for an input audio or video file."""

    sha256: str
    duration: float
    has_audio: bool
    has_video: bool
    sample_rate: int | None
    channels: int | None


class CommandRecorder:
    """Run ffmpeg/ffprobe commands and record them with ``<input>`` placeholders."""

    def __init__(self, input_path: Path) -> None:
        self._input_str = str(input_path)
        self.commands: list[str] = []

    def run(
        self,
        template: list[str],
        *,
        text: bool = False,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
        """Run a command template after substituting ``<input>``, recording it on success."""
        concrete = [arg.replace(INPUT_PLACEHOLDER, self._input_str) for arg in template]
        try:
            result = subprocess.run(
                concrete,
                capture_output=True,
                text=text,
                timeout=timeout,
                check=False,
            )
        except OSError as exc:
            tool = template[0] if template else "command"
            raise RuntimeError(f"{tool} not available: {exc}") from exc
        if result.returncode != 0:
            if isinstance(result.stderr, bytes):
                detail = result.stderr.decode("utf-8", errors="replace").strip()
            else:
                detail = result.stderr.strip()
            tool = template[0] if template else "command"
            raise RuntimeError(f"{tool} failed: {detail or f'exit code {result.returncode}'}")
        self.commands.append(shlex.join(template))
        return result


def probe_tool_version(tool: str) -> str:
    """Return the version token from ``<tool> -version``."""
    try:
        result = subprocess.run(
            [tool, "-version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"{tool} not available: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"{tool} exited with status {result.returncode}")
    lines = result.stdout.splitlines()
    if not lines:
        raise RuntimeError(f"{tool} produced no version output")
    words = lines[0].split()
    if len(words) < 3:
        raise RuntimeError(f"unexpected {tool} version line")
    return words[2]


def sha256_file(path: Path) -> str:
    """Return the lower-case SHA-256 hex digest of ``path``."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _parse_positive_float(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        value = float(str(raw))
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0.0:
        return None
    return value


def probe_media(input_path: Path, recorder: CommandRecorder) -> MediaInfo:
    """Probe duration and audio/video stream properties with ``ffprobe``."""
    if not input_path.is_file():
        raise ValueError(f"input file does not exist: {input_path}")

    template = [
        "ffprobe",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        INPUT_PLACEHOLDER,
    ]
    result = recorder.run(template, text=True)
    stdout = result.stdout if isinstance(result.stdout, str) else result.stdout.decode("utf-8")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe returned invalid JSON: {exc}") from exc

    streams = payload.get("streams", [])
    if not isinstance(streams, list):
        raise ValueError("ffprobe returned invalid stream list")

    audio_streams = [s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"]
    video_streams = [s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"]

    raw_duration = _parse_positive_float(payload.get("format", {}).get("duration"))
    if raw_duration is None and audio_streams:
        raw_duration = _parse_positive_float(audio_streams[0].get("duration"))
    if raw_duration is None and video_streams:
        raw_duration = _parse_positive_float(video_streams[0].get("duration"))
    if raw_duration is None:
        raise ValueError("input media has no readable positive duration")

    duration = round(raw_duration, 3)
    if duration <= 0.0:
        raise ValueError("input media duration rounds to zero")

    sample_rate: int | None = None
    channels: int | None = None
    if audio_streams:
        first_audio = audio_streams[0]
        try:
            sample_rate = int(first_audio["sample_rate"])
            channels = int(first_audio["channels"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("audio stream missing sample_rate or channels") from exc
        if sample_rate <= 0 or channels <= 0:
            raise ValueError("audio stream has invalid sample_rate or channels")

    return MediaInfo(
        sha256=sha256_file(input_path),
        duration=duration,
        has_audio=bool(audio_streams),
        has_video=bool(video_streams),
        sample_rate=sample_rate,
        channels=channels,
    )
