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
