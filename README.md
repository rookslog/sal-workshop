# sal

A command-line tool for reviewing audio and video takes side by side. It draws charts of a clip
(waveform, loudness, cuts, black and frozen stretches), builds a local review page that plays
several takes in step with their charts, and keeps time-stamped comments as plain files.

**Status: early and changing.** The name is a placeholder. Nothing here is released yet.

## What works today

- `sal doctor` — reports the versions of Python, ffmpeg and ffprobe it found.
- `sal view render VIEW --input FILE --out DIR` — draws a chart of a clip (`audio_basic`,
  `av_transition`) and writes `view.png` with `view.json`, which says where every time sits in the
  picture.
- `sal round new` and `sal round build` — describe a set of takes to compare and build a review
  page for them. A round keeps its own copy of each take (a clone where the file system allows,
  so it costs no space) and is its folder: it can be moved or renamed whole, and exporting again
  over the same file name does not change a round that exists.
- The review page — takes play together and you switch between them without losing your place;
  level matching; looping a stretch; the active take's picture large beside the charts; keys for
  everything (`Space`, `1`–`9`, arrows, `L`, `C`, `Esc`). Sound-only takes work too. Chrome is the
  browser it is developed against. In WebKit as Playwright ships it, takes can sit a few tenths of
  a second apart for some seconds after a seek; Safari itself has not been tried.
- `sal round serve rounds/ID` — serves one round's page on this machine only (the loopback address,
  and only to its own page) so that comments written on the page are saved, one plain JSON file
  per comment, under `records/comments/`. The page lists the comments in order of time, with the
  answers to each underneath, and keeps the list current. The command prints the page's address;
  it takes a free port unless `--port N` asks for one (use that to keep the same address).
- `sal comment add`, `sal comment answer`, `sal inbox` — file a comment from the command line, reply
  to one, and list the comments nobody has answered yet.

## Requirements

Python 3.12, [uv](https://docs.astral.sh/uv/), and `ffmpeg` with `ffprobe` on the `PATH`. Developed
on macOS.

```sh
make setup    # create the environment
make check    # lint, format check, type check, tests, leak check
.venv/bin/sal --help
```

The browser tests use Playwright: `.venv/bin/python -m playwright install chromium webkit`.

## How this is built

Most of the code and tests here are written by AI coding agents, directed by the maintainer. A lead
agent writes a test design for each change; a second agent writes the tests from that design; a
third agent writes the code until those tests pass, without changing them. Every change is read
before it lands, and lands as one commit after the whole check passes. `AGENTS.md` holds the rules
the agents work under.

No media is kept in this repository: tests make their own with `ffmpeg`.

## Licence

No licence has been chosen yet. Until one is, all rights are reserved: you are welcome to read the
code, but it is not yet offered for reuse. See `CONTRIBUTING.md`.
