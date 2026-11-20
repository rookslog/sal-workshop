# For agents working in this repository

## Commands

- `make setup` creates the environment (`uv sync`). Run it once; it needs the network.
- `make check` is the gate: lint, format check, type check, tests, leak check. It must pass before
  work is handed back.
- Run tools through the environment: `.venv/bin/python -m pytest`, `.venv/bin/python -m ruff check .`,
  `.venv/bin/python -m ruff format .`, `.venv/bin/python -m mypy`.
- The command itself: `.venv/bin/sal --help`.

## Rules

1. Files stay generic. No real project, track or artist names, no home-directory paths, no links to
   media or file-sharing sites, no email address other than the repository's own. `tools/leak_check.py`
   enforces this; build any forbidden string a test needs at run time, from parts.
2. Never push and never add a remote.
3. Never change or delete an existing test. If a test looks wrong, say so in your report and leave
   it. You may add tests.
4. Never weaken the gate: no skipped tests, no ignored lint rules, no `type: ignore` without the
   reason on the same line.
5. Tests make their own media with `ffmpeg`'s generators. Nothing copyrighted is committed.
6. Say what you measured, not how something sounds or looks.
7. "I could not make this pass, and here is why" is a good result. A passing gate reached by
   changing what is checked is not.
