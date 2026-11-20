# Contributing

This project is at an early stage and has no licence yet, so it is not taking code contributions
for now. Reports of problems are welcome as issues.

When that changes, these will be the rules, and they already bind the agents that write the code:

- One reviewable change per pull request, with tests for any change in behaviour.
- `make check` passes: lint, format check, type check, tests, leak check.
- Say plainly if a change, or part of it, was written by an AI tool, and which one. Do not hide it
  behind a person's name.
- No media files, no private names or paths, no secrets. `tools/leak_check.py` refuses them.
- Existing tests are not changed or removed to make a change pass.

See `AGENTS.md` for the rules agents follow in this repository.
