# Changelog

## Unreleased

- Project skeleton: the `sal` command, the check gate.
- Views: `audio_basic` (waveform, loudness) and `av_transition` (filmstrip, cuts, black and frozen
  stretches, waveform, loudness), each with a `view.json` that maps time to pixels.
- Rounds: `sal round new`, `sal round build`, and the review page (synchronised playback, switching,
  level matching, looping).
- The leak check refuses media files, large files and private strings in files and commit messages.
- Comments: one file per comment, never edited; `sal round serve` (loopback only, byte ranges),
  saving from the page, `sal comment add`, `sal comment answer`, `sal inbox`.
