"""Command-line interface for sal."""

import argparse
import json
import platform
import sys
from collections.abc import Callable
from pathlib import Path

from sal import __version__
from sal.rounds import build_round, create_round
from sal.views import render_view
from sal.views.media import probe_tool_version


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Report tool and runtime versions."""
    try:
        versions = {
            "ffmpeg": probe_tool_version("ffmpeg"),
            "ffprobe": probe_tool_version("ffprobe"),
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
    """Render a view to PNG and JSON."""
    out_dir = Path(args.out)
    try:
        png_path, json_path = render_view(
            view_name=args.view,
            input_path=Path(args.input),
            out_dir=out_dir,
            span_arg=args.span,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps({"png": str(png_path), "json": str(json_path)}, sort_keys=True))
    return 0


def _cmd_round_new(args: argparse.Namespace) -> int:
    """Create a new review round."""
    try:
        round_path = create_round(
            round_id=args.id,
            question=args.question,
            take_specs=args.takes,
            control=args.control,
            view_name=args.view,
            blind=args.blind,
            project=args.project,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps({"round": round_path.as_posix()}, sort_keys=True))
    return 0


def _cmd_round_build(args: argparse.Namespace) -> int:
    """Build a review round page."""
    try:
        build_dir, chosen_seed = build_round(Path(args.round_dir), seed=args.seed)
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps({"build": build_dir.as_posix(), "seed": chosen_seed}, sort_keys=True))
    return 0


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
    view_render_parser.add_argument("view", metavar="VIEW", help="Name of the view to render.")
    view_render_parser.add_argument(
        "--input", required=True, metavar="PATH", help="Input media path."
    )
    view_render_parser.add_argument(
        "--span",
        default=None,
        metavar="A:B",
        help="Optional time span in seconds (default: full file).",
    )
    view_render_parser.add_argument(
        "--out", required=True, metavar="DIR", help="Output directory for view.png and view.json."
    )
    view_render_parser.set_defaults(func=_cmd_view_render)

    round_parser = subparsers.add_parser("round", help="Manage review rounds.")
    round_parser.set_defaults(help_parser=round_parser)
    round_sub = round_parser.add_subparsers(dest="subcommand")
    round_new_parser = round_sub.add_parser("new", help="Create a new review round.")
    round_new_parser.add_argument("id", metavar="ID", help="Round identifier.")
    round_new_parser.add_argument(
        "--question", required=True, metavar="TEXT", help="Question for the reviewer."
    )
    round_new_parser.add_argument(
        "--take",
        dest="takes",
        action="append",
        required=True,
        metavar="PATH[::LABEL]",
        help="Take file path and optional label.",
    )
    round_new_parser.add_argument(
        "--control",
        required=True,
        type=int,
        metavar="N",
        help="1-based index of the control take.",
    )
    round_new_parser.add_argument(
        "--view", required=True, metavar="VIEW", help="View name to render for each take."
    )
    round_new_parser.add_argument(
        "--blind", action="store_true", help="Hide take labels and order takes from a seed."
    )
    round_new_parser.add_argument(
        "--project", default=None, metavar="NAME", help="Optional project name."
    )
    round_new_parser.set_defaults(func=_cmd_round_new)

    round_build_parser = round_sub.add_parser("build", help="Build a review round.")
    round_build_parser.add_argument(
        "round_dir", metavar="ROUND_DIR", help="Path to the round directory."
    )
    round_build_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="N",
        help="Optional non-negative seed for blind take order.",
    )
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
