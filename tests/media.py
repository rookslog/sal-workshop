import hashlib
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image


def make_wav(
    path: Path,
    segments: list[tuple[float, float, float]],
    rate: int = 48000,
    channels: int = 2,
    gains: list[float] | None = None,
) -> Path:
    channel_gains = np.ones(channels) if gains is None else np.asarray(gains)
    with wave.open(str(path), "wb") as output:
        output.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        for seconds, frequency_hz, amplitude in segments:
            count = round(seconds * rate)
            if count == 0:
                continue
            # ffmpeg supplies the sine; NumPy applies gains and rounds to the specified PCM.
            source = f"aevalsrc=sin(2*PI*{frequency_hz}*n/{rate}):s={rate}"
            carrier = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    source,
                    "-af",
                    f"atrim=end_sample={count}",
                    "-f",
                    "f64le",
                    "pipe:1",
                ],
                timeout=120,
            )
            sine = np.frombuffer(carrier, dtype="<f8")
            pcm = np.rint((amplitude * channel_gains * 32767) * sine[:, None])
            output.writeframes(pcm.astype("<i2").tobytes())
    return path


def sha256_of(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_png(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.array(image.convert("RGBA"), dtype=np.uint8)


type RGB = tuple[int, int, int]
type VideoSegment = tuple[str, int, RGB] | tuple[str, int]


def make_video(
    path: Path,
    plan: list[VideoSegment],
    fps: str = "25",
    size: tuple[int, int] = (320, 180),
    audio: Path | None = None,
) -> Path:
    width, height = size
    frames = bytearray()
    previous: bytes | None = None
    moving_frames = 0
    for segment in plan:
        kind, count = segment[:2]
        if kind == "move":
            _, _, colour = segment
            picture = np.empty((height, width, 3), dtype=np.uint8)
            for _ in range(count):
                picture[:] = colour
                bar_x = (4 * moving_frames) % width
                picture[:, bar_x : bar_x + 16] = 255
                previous = picture.tobytes()
                frames.extend(previous)
                moving_frames += 1
        elif kind == "black":
            previous = np.zeros((height, width, 3), dtype=np.uint8).tobytes()
            frames.extend(previous * count)
        elif kind == "freeze":
            assert previous is not None, "freeze needs a previous frame"
            frames.extend(previous * count)
        else:
            raise ValueError(f"Unknown video segment: {kind}")

    args = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-framerate",
        fps,
        "-i",
        "-",
    ]
    if audio is not None:
        args.extend(["-i", str(audio)])
    args.extend(
        ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-g", "25"]
    )
    if audio is not None:
        args.extend(["-c:a", "aac", "-b:a", "192k"])
    subprocess.run([*args, str(path)], input=bytes(frames), check=True, timeout=120)
    return path


def frame_time(n: int, fps: str) -> float:
    if fps == "25":
        return n / 25
    if fps == "24000/1001":
        return n * 1001 / 24000
    raise ValueError(f"Unsupported fixture frame rate: {fps}")
