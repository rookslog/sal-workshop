"""Command-line interface for sal."""

import argparse
import json
import platform
import subprocess
import sys
from collections.abc import Callable

from sal import __version__


def _probe_tool_version(tool: str) -> str:
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


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Report tool and runtime versions."""
    try:
        versions = {
            "ffmpeg": _probe_tool_version("ffmpeg"),
            "ffprobe": _probe_tool_version("ffprobe"),
            "python": platform.python_version(),
            "sal": __version__,
        }
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(versions, sort_keys=True))
    else:
        for key in ("sal", "python", "ffmpeg", "ffprobe"):
            print(f"{key} {versions[key]}")
    return 0


def _not_implemented() -> int:
    """Print the stub message to stderr and return a non-zero exit code."""
    print("not implemented", file=sys.stderr)
    return 1


def _cmd_view_render(args: argparse.Namespace) -> int:
    """Render a view (stub)."""
    del args
    return _not_implemented()


def _cmd_round_new(args: argparse.Namespace) -> int:
    """Create a new review round (stub)."""
    del args
    return _not_implemented()


def _cmd_round_build(args: argparse.Namespace) -> int:
    """Build a review round page (stub)."""
    del args
    return _not_implemented()


def _cmd_round_serve(args: argparse.Namespace) -> int:
    """Serve a review round locally (stub)."""
    del args
    return _not_implemented()


def _cmd_comment_add(args: argparse.Namespace) -> int:
    """Add a review comment (stub)."""
    del args
    return _not_implemented()


def _cmd_comment_answer(args: argparse.Namespace) -> int:
    """Answer a review comment (stub)."""
    del args
    return _not_implemented()


def _cmd_inbox(args: argparse.Namespace) -> int:
    """Show pending review items (stub)."""
    del args
    return _not_implemented()


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI argument parser."""
    parser = argparse.ArgumentParser(prog="sal", description="Sonic analysis and review CLI.")
    parser.add_argument("--version", action="version", version=f"sal {__version__}")
    parser.set_defaults(help_parser=parser)
    subparsers = parser.add_subparsers(dest="command")

    doctor_parser = subparsers.add_parser(
        "doctor", help="Check runtime environment and external tools."
    )
    doctor_parser.add_argument(
        "--json", action="store_true", help="Emit versions as a single JSON line."
    )
    doctor_parser.set_defaults(func=_cmd_doctor)

    view_parser = subparsers.add_parser("view", help="Inspect and render views.")
    view_parser.set_defaults(help_parser=view_parser)
    view_sub = view_parser.add_subparsers(dest="subcommand")
    view_render_parser = view_sub.add_parser("render", help="Render a view for an input file.")
    view_render_parser.set_defaults(func=_cmd_view_render)

    round_parser = subparsers.add_parser("round", help="Manage review rounds.")
    round_parser.set_defaults(help_parser=round_parser)
    round_sub = round_parser.add_subparsers(dest="subcommand")
    round_new_parser = round_sub.add_parser("new", help="Create a new review round.")
    round_new_parser.set_defaults(func=_cmd_round_new)
    round_build_parser = round_sub.add_parser("build", help="Build a review round.")
    round_build_parser.set_defaults(func=_cmd_round_build)
    round_serve_parser = round_sub.add_parser("serve", help="Serve a review round.")
    round_serve_parser.set_defaults(func=_cmd_round_serve)

    comment_parser = subparsers.add_parser("comment", help="Manage review comments.")
    comment_parser.set_defaults(help_parser=comment_parser)
    comment_sub = comment_parser.add_subparsers(dest="subcommand")
    comment_add_parser = comment_sub.add_parser("add", help="Add a review comment.")
    comment_add_parser.set_defaults(func=_cmd_comment_add)
    comment_answer_parser = comment_sub.add_parser("answer", help="Answer a review comment.")
    comment_answer_parser.set_defaults(func=_cmd_comment_answer)

    inbox_parser = subparsers.add_parser("inbox", help="List open comments and questions.")
    inbox_parser.set_defaults(func=_cmd_inbox)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the sal CLI and return an exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    func: Callable[[argparse.Namespace], int] | None = getattr(args, "func", None)
    if func is None:
        help_parser: argparse.ArgumentParser = getattr(args, "help_parser", parser)
        help_parser.print_help(sys.stderr)
        return 2
    return func(args)
