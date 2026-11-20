"""Creation and building of review rounds."""

from __future__ import annotations

import html
import json
import math
import os
import random
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from sal.views import VIEWS, render_view
from sal.views.media import CommandRecorder, probe_media, sha256_file

ROUND_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\Z")
TAKE_EXT_RE = re.compile(r"^\.[A-Za-z0-9]+\Z")


def _format_json(payload: Mapping[str, Any]) -> str:
    """Serialize a JSON object in the canonical slice-1 format."""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a temporary file in the same directory."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _parse_take_spec(spec: str, index: int) -> tuple[str, str]:
    """Parse ``PATH[::LABEL]`` into ``(path, label)`` for 1-based take ``index``."""
    if "::" in spec:
        raw_path, _, label = spec.partition("::")
        if not label:
            raise ValueError(f"take {index} has an empty label after '::'")
    else:
        raw_path = spec
        label = f"take {index}"
    if not raw_path:
        raise ValueError(f"take {index} has an empty path")
    return raw_path, label


def _measure_integrated_lufs(input_path: Path) -> float:
    """Measure EBU R 128 integrated loudness (LUFS) of ``input_path`` via ffmpeg."""
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(input_path),
        "-map",
        "0:a:0",
        "-af",
        "ebur128=metadata=1,ametadata=mode=print:file=-",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    except OSError as exc:
        raise RuntimeError(f"ffmpeg not available: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"ffmpeg ebur128 failed for {input_path}: {detail}")

    integrated: float | None = None
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("lavfi.r128.I="):
            value_str = line.split("=", 1)[1]
            try:
                parsed = float(value_str)
            except ValueError:
                continue
            if math.isfinite(parsed):
                integrated = parsed

    if integrated is None:
        raise ValueError(f"ffmpeg ebur128 produced no integrated loudness for {input_path}")
    return round(integrated, 3)


def _copy_take(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst``, cloning with ``cp -c`` when supported."""
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["cp", "-c", str(src), str(dst)],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                return
        except OSError:
            pass
        dst.unlink(missing_ok=True)
    shutil.copyfile(src, dst)


def _publish_round(
    stage_media: Path,
    stage_round_json: Path,
    round_dir: Path,
    round_json_path: Path,
) -> None:
    """Atomically publish ``round.json`` and move staged media files into ``round_dir``."""
    media_dir = round_dir / "media"
    if media_dir.is_symlink() or (media_dir.exists() and not media_dir.is_dir()):
        raise ValueError(f"round media path exists and is not a directory: {media_dir}")

    try:
        os.link(stage_round_json, round_json_path)
    except FileExistsError as exc:
        raise ValueError(f"round already exists: {round_json_path}") from exc

    moved_files: list[Path] = []
    created_media_dir = False
    try:
        if not media_dir.exists() and not media_dir.is_symlink():
            os.replace(stage_media, media_dir)
            created_media_dir = True
        else:
            for src_file in stage_media.iterdir():
                dst_file = media_dir / src_file.name
                os.replace(src_file, dst_file)
                moved_files.append(dst_file)
    except BaseException:
        round_json_path.unlink(missing_ok=True)
        if created_media_dir and media_dir.is_dir() and not media_dir.is_symlink():
            shutil.rmtree(media_dir, ignore_errors=True)
        else:
            for dst_file in moved_files:
                dst_file.unlink(missing_ok=True)
        raise


def create_round(
    round_id: str,
    *,
    question: str,
    take_specs: list[str],
    control: int,
    view_name: str,
    blind: bool = False,
    project: str | None = None,
    root: Path | None = None,
) -> Path:
    """Validate inputs, copy and probe media, and write ``rounds/<id>/round.json``."""
    if not ROUND_ID_RE.fullmatch(round_id):
        raise ValueError(f"invalid round id {round_id!r}: must match ^[a-z0-9][a-z0-9-]{{0,62}}$")
    if not question:
        raise ValueError("question must not be empty")
    if len(take_specs) < 2 or len(take_specs) > 9:
        raise ValueError(f"expected between 2 and 9 takes, got {len(take_specs)}")
    if control < 1 or control > len(take_specs):
        raise ValueError(f"--control {control} out of range for {len(take_specs)} takes")
    if view_name not in VIEWS:
        available = ", ".join(sorted(VIEWS)) or "(none)"
        raise ValueError(f"unknown view {view_name!r}; available views: {available}")

    base = Path(".") if root is None else root
    round_dir = base / "rounds" / round_id
    if round_dir.exists() and not round_dir.is_dir():
        raise ValueError(f"round path exists and is not a directory: {round_dir}")
    round_json_path = round_dir / "round.json"
    if round_json_path.exists() or round_json_path.is_symlink():
        raise ValueError(f"round already exists: {round_json_path}")

    resolved_specs: list[tuple[Path, str]] = []
    for index, spec in enumerate(take_specs, start=1):
        raw_path, label = _parse_take_spec(spec, index)
        candidate = Path(raw_path)
        if not candidate.is_file():
            raise ValueError(f"take file does not exist: {raw_path}")
        resolved = candidate.resolve()
        if not TAKE_EXT_RE.fullmatch(resolved.suffix):
            raise ValueError(f"take file must have an alphanumeric extension: {resolved.name}")
        resolved_specs.append((resolved, label))

    view_spec = VIEWS[view_name]
    seen_hashes: set[str] = set()
    takes_payload: list[dict[str, Any]] = []

    round_dir.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(dir=round_dir, prefix=".new-tmp-"))
    stage_media = stage_dir / "media"
    stage_media.mkdir()
    try:
        for index, (resolved, label) in enumerate(resolved_specs, start=1):
            tmp_copy = stage_media / f".take-{index}{resolved.suffix}"
            _copy_take(resolved, tmp_copy)
            os.chmod(tmp_copy, 0o444)

            recorder = CommandRecorder(tmp_copy)
            media = probe_media(tmp_copy, recorder)
            if view_spec.requires_audio and not media.has_audio:
                raise ValueError(f"take {index} has no audio stream required by view {view_name!r}")
            if view_spec.requires_video and not media.has_video:
                raise ValueError(f"take {index} has no video stream required by view {view_name!r}")
            if media.sha256 in seen_hashes:
                raise ValueError(f"duplicate take sha256: {media.sha256}")
            seen_hashes.add(media.sha256)

            rel_media_path = f"media/{media.sha256}{resolved.suffix}"
            os.replace(tmp_copy, stage_dir / rel_media_path)

            takes_payload.append(
                {
                    "sha256": media.sha256,
                    "path": rel_media_path,
                    "source": str(resolved),
                    "duration": media.duration,
                    "label": label,
                    "control": index == control,
                }
            )

        payload: dict[str, Any] = {
            "schema": "sal.round/1",
            "id": round_id,
            "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "project": project,
            "question": question,
            "blind": blind,
            "views": [view_name],
            "takes": takes_payload,
        }

        encoded = _format_json(payload).encode("utf-8")
        stage_round_json = stage_dir / "round.json"
        stage_round_json.write_bytes(encoded)
        _publish_round(stage_media, stage_round_json, round_dir, round_json_path)
    except BaseException:
        shutil.rmtree(stage_dir, ignore_errors=True)
        try:
            round_dir.rmdir()
        except OSError:
            pass
        raise
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)

    return round_json_path


def _load_round_json(round_json_path: Path) -> dict[str, Any]:
    """Read and validate the essential fields of ``round.json``."""
    if not round_json_path.is_file():
        raise ValueError(f"round file does not exist: {round_json_path}")
    try:
        data = json.loads(round_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read round file {round_json_path}: {exc}") from exc

    if not isinstance(data, dict) or data.get("schema") != "sal.round/1":
        raise ValueError(f"invalid round schema in {round_json_path}")
    views = data.get("views")
    if not isinstance(views, list) or len(views) != 1 or views[0] not in VIEWS:
        raise ValueError(f"invalid view configuration in {round_json_path}")
    takes = data.get("takes")
    if not isinstance(takes, list) or not (2 <= len(takes) <= 9):
        raise ValueError(f"invalid takes list in {round_json_path}")
    return data


def _resolve_seed(round_dir: Path, explicit_seed: int | None) -> int:
    """Choose the build seed from CLI flag, existing ``build.private.json``, or RNG."""
    if explicit_seed is not None:
        if explicit_seed < 0:
            raise ValueError("--seed must be a non-negative integer")
        return explicit_seed

    private_path = round_dir / "build.private.json"
    if private_path.is_file():
        try:
            private_data = json.loads(private_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read existing {private_path}: {exc}") from exc
        stored_seed = private_data.get("seed") if isinstance(private_data, dict) else None
        if isinstance(stored_seed, int) and not isinstance(stored_seed, bool) and stored_seed >= 0:
            return stored_seed

    return secrets.randbelow(2**31)


def _render_index_html(question: str, page_json_text: str) -> bytes:
    """Fill the packaged HTML template with the question and embedded page JSON."""
    template = files("sal.page").joinpath("index.html").read_text(encoding="utf-8")
    safe_json = page_json_text.rstrip("\n").replace("<", "\\u003c")
    safe_question = html.escape(question, quote=False)
    rendered = template.replace("__QUESTION_TEXT__", safe_question).replace(
        "__PAGE_DATA_JSON__", safe_json
    )
    return rendered.encode("utf-8")


def _swap_build_dir(stage_dir: Path, build_dir: Path) -> None:
    """Replace ``build_dir`` with ``stage_dir``, restoring the prior directory on failure."""
    backup_dir: Path | None = None
    if build_dir.exists() or build_dir.is_symlink():
        backup_dir = Path(tempfile.mkdtemp(dir=build_dir.parent, prefix=".build-old-"))
        shutil.rmtree(backup_dir)
        os.replace(build_dir, backup_dir)
    try:
        os.replace(stage_dir, build_dir)
    except BaseException:
        if backup_dir is not None and backup_dir.exists() and not build_dir.exists():
            os.replace(backup_dir, build_dir)
        raise
    if backup_dir is not None and backup_dir.exists():
        shutil.rmtree(backup_dir)


def _resolve_round_take_path(round_dir: Path, raw_path: Any) -> Path:
    """Resolve a take's ``path`` relative to ``round_dir`` inside ``media/``."""
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("take path must be a non-empty string")
    rel = Path(raw_path)
    if rel.is_absolute() or ".." in rel.parts or len(rel.parts) != 2 or rel.parts[0] != "media":
        raise ValueError(f"take path must be media/<file> inside round: {raw_path!r}")
    media_dir = round_dir / "media"
    if media_dir.is_symlink() or not media_dir.is_dir():
        raise ValueError(f"round media directory is missing or invalid: {media_dir}")
    take_path = round_dir / rel
    if take_path.is_symlink() or not take_path.is_file():
        raise ValueError(f"take file is missing or invalid: {take_path}")
    resolved_take = take_path.resolve()
    if not resolved_take.is_relative_to(media_dir.resolve()):
        raise ValueError(f"take path escapes round media/: {raw_path!r}")
    return resolved_take


def build_round(round_dir: Path, *, seed: int | None = None) -> tuple[Path, int]:
    """Verify take integrity, compute loudness/order, render views, and write ``build/``."""
    round_data = _load_round_json(round_dir / "round.json")
    chosen_seed = _resolve_seed(round_dir, seed)

    view_name = str(round_data["views"][0])
    takes: list[dict[str, Any]] = round_data["takes"]
    blind = bool(round_data["blind"])
    question = str(round_data["question"])
    round_id = str(round_data["id"])

    # Re-verify every take file before creating or touching anything under build/.
    take_paths_by_sha: dict[str, Path] = {}
    for take in takes:
        take_path = _resolve_round_take_path(round_dir, take.get("path"))
        expected_sha = str(take["sha256"])
        actual_sha = sha256_file(take_path)
        if actual_sha != expected_sha:
            raise ValueError(
                f"sha256 mismatch for {take_path}: expected {expected_sha}, got {actual_sha}"
            )
        take_paths_by_sha[expected_sha] = take_path

    if blind:
        order = list(range(len(takes)))
        random.Random(chosen_seed).shuffle(order)
        ordered = [(takes[idx], chr(ord("A") + pos)) for pos, idx in enumerate(order)]
    else:
        ordered = [(take, str(take["label"])) for take in takes]

    lufs_by_sha: dict[str, float] = {}
    for take, _ in ordered:
        sha = str(take["sha256"])
        lufs_by_sha[sha] = _measure_integrated_lufs(take_paths_by_sha[sha])

    programme_lufs = [lufs for lufs in lufs_by_sha.values() if lufs > -69.0]
    gain_by_sha: dict[str, float] = {}
    if not programme_lufs:
        for sha in lufs_by_sha:
            gain_by_sha[sha] = 0.0
    else:
        min_lufs = min(programme_lufs)
        for sha, lufs in lufs_by_sha.items():
            if lufs <= -69.0:
                gain_by_sha[sha] = 0.0
            else:
                raw_gain = round(min_lufs - lufs, 2)
                gain_by_sha[sha] = 0.0 if raw_gain >= 0.0 else raw_gain

    stage_dir = Path(tempfile.mkdtemp(dir=round_dir, prefix=".build-tmp-"))
    try:
        page_takes: list[dict[str, Any]] = []
        private_takes: list[dict[str, Any]] = []

        for take, shown in ordered:
            sha = str(take["sha256"])
            source_path = take_paths_by_sha[sha]
            ext = source_path.suffix
            if not TAKE_EXT_RE.fullmatch(ext):
                raise ValueError(f"take file has unsupported extension: {source_path.name}")

            take_stage = stage_dir / "takes" / sha
            take_stage.mkdir(parents=True, exist_ok=True)
            rel_link_target = os.path.relpath(source_path, take_stage.resolve())
            os.symlink(rel_link_target, take_stage / f"media{ext}")

            view_out = take_stage / view_name
            _, view_json_path = render_view(view_name, source_path, view_out)
            view_json = json.loads(view_json_path.read_text(encoding="utf-8"))
            ruler = view_json["ruler"]

            page_takes.append(
                {
                    "shown": shown,
                    "sha256": sha,
                    "duration": take["duration"],
                    "gain_db": gain_by_sha[sha],
                    "media": f"takes/{sha}/media{ext}",
                    "view": {
                        "name": view_name,
                        "png": f"takes/{sha}/{view_name}/view.png",
                        "width": int(view_json["image"]["width"]),
                        "height": int(view_json["image"]["height"]),
                        "ruler": {
                            "x0": int(ruler["x0"]),
                            "x1": int(ruler["x1"]),
                            "t0": float(ruler["t0"]),
                            "t1": float(ruler["t1"]),
                        },
                    },
                }
            )
            private_takes.append(
                {
                    "sha256": sha,
                    "shown": shown,
                    "label": str(take["label"]),
                    "control": bool(take["control"]),
                    "lufs": lufs_by_sha[sha],
                    "gain_db": gain_by_sha[sha],
                }
            )

        page_payload: dict[str, Any] = {
            "schema": "sal.page/1",
            "round": round_id,
            "question": question,
            "blind": blind,
            "takes": page_takes,
        }
        page_json_text = _format_json(page_payload)
        (stage_dir / "page.json").write_bytes(page_json_text.encode("utf-8"))
        (stage_dir / "index.html").write_bytes(_render_index_html(question, page_json_text))
        page_js_bytes = files("sal.page").joinpath("page.js").read_bytes()
        (stage_dir / "page.js").write_bytes(page_js_bytes)

        private_payload: dict[str, Any] = {
            "schema": "sal.build-private/1",
            "round": round_id,
            "seed": chosen_seed,
            "takes": private_takes,
        }
        private_bytes = _format_json(private_payload).encode("utf-8")

        build_dir = round_dir / "build"
        _swap_build_dir(stage_dir, build_dir)
        _atomic_write_bytes(round_dir / "build.private.json", private_bytes)
        return build_dir, chosen_seed
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
