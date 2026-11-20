from pathlib import Path
from shutil import copyfile

import pytest
from conftest import http, make_audio_take, make_round, read_json, served

AUDIO_CODECS = {
    ".m4a": "aac",
    ".mp3": "libmp3lame",
    ".wav": "pcm_s16le",
    ".flac": "flac",
    ".opus": "libopus",
    ".ogg": "libopus",
}


@pytest.mark.parametrize(
    ("extensions", "content_types"),
    [
        pytest.param((".mp4", ".m4v"), ("video/mp4", "video/mp4"), id="mp4-m4v"),
        pytest.param((".m4a", ".mp3"), ("audio/mp4", "audio/mpeg"), id="m4a-mp3"),
        pytest.param((".wav", ".flac"), ("audio/wav", "audio/flac"), id="wav-flac"),
        pytest.param((".opus", ".ogg"), ("audio/ogg", "audio/ogg"), id="opus-ogg"),
    ],
)
def test_d5_serves_media_with_exact_content_type(
    tmp_path: Path,
    take_low_mp4: Path,
    take_high_mp4: Path,
    extensions: tuple[str, str],
    content_types: tuple[str, str],
) -> None:
    if extensions == (".mp4", ".m4v"):
        # Keep the MP4 container and audio track with its alternate file extension.
        renamed = tmp_path / "second.m4v"
        copyfile(take_high_mp4, renamed)
        takes = [take_low_mp4, renamed]
    else:
        takes = [
            make_audio_take(tmp_path / f"take{extension}", pitch, AUDIO_CODECS[extension])
            for extension, pitch in zip(extensions, (440, 660), strict=True)
        ]
    round_dir = make_round(tmp_path, takes)
    media = read_json(round_dir / "build" / "page.json")["takes"]
    with served(round_dir) as (url, _):
        for take, extension, expected in zip(media, extensions, content_types, strict=True):
            assert Path(take["media"]).suffix == extension
            status, headers, _ = http("GET", url + take["media"])
            assert status == 200, extension
            assert headers["content-type"] == expected, extension
